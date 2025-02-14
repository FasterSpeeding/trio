# coding: utf-8

# Little utilities we use internally

from abc import ABCMeta
import os
import signal
import sys
import pathlib
from functools import wraps, update_wrapper
import typing as t
import threading
import collections
import inspect

import trio

# Equivalent to the C function raise(), which Python doesn't wrap
if os.name == "nt":
    # On windows, os.kill exists but is really weird.
    #
    # If you give it CTRL_C_EVENT or CTRL_BREAK_EVENT, it tries to deliver
    # those using GenerateConsoleCtrlEvent. But I found that when I tried
    # to run my test normally, it would freeze waiting... unless I added
    # print statements, in which case the test suddenly worked. So I guess
    # these signals are only delivered if/when you access the console? I
    # don't really know what was going on there. From reading the
    # GenerateConsoleCtrlEvent docs I don't know how it worked at all.
    #
    # I later spent a bunch of time trying to make GenerateConsoleCtrlEvent
    # work for creating synthetic control-C events, and... failed
    # utterly. There are lots of details in the code and comments
    # removed/added at this commit:
    #     https://github.com/python-trio/trio/commit/95843654173e3e826c34d70a90b369ba6edf2c23
    #
    # OTOH, if you pass os.kill any *other* signal number... then CPython
    # just calls TerminateProcess (wtf).
    #
    # So, anyway, os.kill is not so useful for testing purposes. Instead
    # we use raise():
    #
    #   https://msdn.microsoft.com/en-us/library/dwwzkt4c.aspx
    #
    # Have to import cffi inside the 'if os.name' block because we don't
    # depend on cffi on non-Windows platforms. (It would be easy to switch
    # this to ctypes though if we ever remove the cffi dependency.)
    #
    # Some more information:
    #   https://bugs.python.org/issue26350
    #
    # Anyway, we use this for two things:
    # - redelivering unhandled signals
    # - generating synthetic signals for tests
    # and for both of those purposes, 'raise' works fine.
    import cffi

    _ffi = cffi.FFI()
    _ffi.cdef("int raise(int);")
    _lib = _ffi.dlopen("api-ms-win-crt-runtime-l1-1-0.dll")
    signal_raise = getattr(_lib, "raise")
else:

    def signal_raise(signum):
        signal.pthread_kill(threading.get_ident(), signum)


# See: #461 as to why this is needed.
# The gist is that threading.main_thread() has the capability to lie to us
# if somebody else edits the threading ident cache to replace the main
# thread; causing threading.current_thread() to return a _DummyThread,
# causing the C-c check to fail, and so on.
# Trying to use signal out of the main thread will fail, so we can then
# reliably check if this is the main thread without relying on a
# potentially modified threading.
def is_main_thread():
    """Attempt to reliably check if we are in the main thread."""
    try:
        signal.signal(signal.SIGINT, signal.getsignal(signal.SIGINT))
        return True
    except (TypeError, ValueError):
        return False


######
# Call the function and get the coroutine object, while giving helpful
# errors for common mistakes. Returns coroutine object.
######
def coroutine_or_error(async_fn, *args):
    def _return_value_looks_like_wrong_library(value):
        # Returned by legacy @asyncio.coroutine functions, which includes
        # a surprising proportion of asyncio builtins.
        if isinstance(value, collections.abc.Generator):
            return True
        # The protocol for detecting an asyncio Future-like object
        if getattr(value, "_asyncio_future_blocking", None) is not None:
            return True
        # This janky check catches tornado Futures and twisted Deferreds.
        # By the time we're calling this function, we already know
        # something has gone wrong, so a heuristic is pretty safe.
        if value.__class__.__name__ in ("Future", "Deferred"):
            return True
        return False

    try:
        coro = async_fn(*args)

    except TypeError:
        # Give good error for: nursery.start_soon(trio.sleep(1))
        if isinstance(async_fn, collections.abc.Coroutine):
            # explicitly close coroutine to avoid RuntimeWarning
            async_fn.close()

            raise TypeError(
                "Trio was expecting an async function, but instead it got "
                "a coroutine object {async_fn!r}\n"
                "\n"
                "Probably you did something like:\n"
                "\n"
                "  trio.run({async_fn.__name__}(...))            # incorrect!\n"
                "  nursery.start_soon({async_fn.__name__}(...))  # incorrect!\n"
                "\n"
                "Instead, you want (notice the parentheses!):\n"
                "\n"
                "  trio.run({async_fn.__name__}, ...)            # correct!\n"
                "  nursery.start_soon({async_fn.__name__}, ...)  # correct!".format(
                    async_fn=async_fn
                )
            ) from None

        # Give good error for: nursery.start_soon(future)
        if _return_value_looks_like_wrong_library(async_fn):
            raise TypeError(
                "Trio was expecting an async function, but instead it got "
                "{!r} – are you trying to use a library written for "
                "asyncio/twisted/tornado or similar? That won't work "
                "without some sort of compatibility shim.".format(async_fn)
            ) from None

        raise

    # We can't check iscoroutinefunction(async_fn), because that will fail
    # for things like functools.partial objects wrapping an async
    # function. So we have to just call it and then check whether the
    # return value is a coroutine object.
    # Note: will not be necessary on python>=3.8, see https://bugs.python.org/issue34890
    if not isinstance(coro, collections.abc.Coroutine):
        # Give good error for: nursery.start_soon(func_returning_future)
        if _return_value_looks_like_wrong_library(coro):
            raise TypeError(
                "Trio got unexpected {!r} – are you trying to use a "
                "library written for asyncio/twisted/tornado or similar? "
                "That won't work without some sort of compatibility shim.".format(coro)
            )

        if inspect.isasyncgen(coro):
            raise TypeError(
                "start_soon expected an async function but got an async "
                "generator {!r}".format(coro)
            )

        # Give good error for: nursery.start_soon(some_sync_fn)
        raise TypeError(
            "Trio expected an async function, but {!r} appears to be "
            "synchronous".format(getattr(async_fn, "__qualname__", async_fn))
        )

    return coro


class ConflictDetector:
    """Detect when two tasks are about to perform operations that would
    conflict.

    Use as a synchronous context manager; if two tasks enter it at the same
    time then the second one raises an error. You can use it when there are
    two pieces of code that *would* collide and need a lock if they ever were
    called at the same time, but that should never happen.

    We use this in particular for things like, making sure that two different
    tasks don't call sendall simultaneously on the same stream.

    """

    def __init__(self, msg):
        self._msg = msg
        self._held = False

    def __enter__(self):
        if self._held:
            raise trio.BusyResourceError(self._msg)
        else:
            self._held = True

    def __exit__(self, *args):
        self._held = False


def async_wraps(cls, wrapped_cls, attr_name):
    """Similar to wraps, but for async wrappers of non-async functions."""

    def decorator(func):
        func.__name__ = attr_name
        func.__qualname__ = ".".join((cls.__qualname__, attr_name))

        func.__doc__ = """Like :meth:`~{}.{}.{}`, but async.

        """.format(
            wrapped_cls.__module__, wrapped_cls.__qualname__, attr_name
        )

        return func

    return decorator


def fixup_module_metadata(module_name, namespace):
    seen_ids = set()

    def fix_one(qualname, name, obj):
        # avoid infinite recursion (relevant when using
        # typing.Generic, for example)
        if id(obj) in seen_ids:
            return
        seen_ids.add(id(obj))

        mod = getattr(obj, "__module__", None)
        if mod is not None and mod.startswith("trio."):
            obj.__module__ = module_name
            # Modules, unlike everything else in Python, put fully-qualitied
            # names into their __name__ attribute. We check for "." to avoid
            # rewriting these.
            if hasattr(obj, "__name__") and "." not in obj.__name__:
                obj.__name__ = name
                obj.__qualname__ = qualname
            if isinstance(obj, type):
                for attr_name, attr_value in obj.__dict__.items():
                    fix_one(objname + "." + attr_name, attr_name, attr_value)

    for objname, obj in namespace.items():
        if not objname.startswith("_"):  # ignore private attributes
            fix_one(objname, objname, obj)


class generic_function:
    """Decorator that makes a function indexable, to communicate
    non-inferrable generic type parameters to a static type checker.

    If you write::

        @generic_function
        def open_memory_channel(max_buffer_size: int) -> Tuple[
            SendChannel[T], ReceiveChannel[T]
        ]: ...

    it is valid at runtime to say ``open_memory_channel[bytes](5)``.
    This behaves identically to ``open_memory_channel(5)`` at runtime,
    and currently won't type-check without a mypy plugin or clever stubs,
    but at least it becomes possible to write those.
    """

    def __init__(self, fn):
        update_wrapper(self, fn)
        self._fn = fn

    def __call__(self, *args, **kwargs):
        return self._fn(*args, **kwargs)

    def __getitem__(self, _):
        return self


class Final(ABCMeta):
    """Metaclass that enforces a class to be final (i.e., subclass not allowed).

    If a class uses this metaclass like this::

        class SomeClass(metaclass=Final):
            pass

    The metaclass will ensure that no sub class can be created.

    Raises
    ------
    - TypeError if a sub class is created
    """

    def __new__(cls, name, bases, cls_namespace):
        for base in bases:
            if isinstance(base, Final):
                raise TypeError(
                    f"{base.__module__}.{base.__qualname__} does not support subclassing"
                )
        return super().__new__(cls, name, bases, cls_namespace)


T = t.TypeVar("T")


class NoPublicConstructor(Final):
    """Metaclass that enforces a class to be final (i.e., subclass not allowed)
    and ensures a private constructor.

    If a class uses this metaclass like this::

        class SomeClass(metaclass=NoPublicConstructor):
            pass

    The metaclass will ensure that no sub class can be created, and that no instance
    can be initialized.

    If you try to instantiate your class (SomeClass()), a TypeError will be thrown.

    Raises
    ------
    - TypeError if a sub class or an instance is created.
    """

    def __call__(cls, *args, **kwargs):
        raise TypeError(
            f"{cls.__module__}.{cls.__qualname__} has no public constructor"
        )

    def _create(cls: t.Type[T], *args: t.Any, **kwargs: t.Any) -> T:
        return super().__call__(*args, **kwargs)  # type: ignore


def name_asyncgen(agen):
    """Return the fully-qualified name of the async generator function
    that produced the async generator iterator *agen*.
    """
    if not hasattr(agen, "ag_code"):  # pragma: no cover
        return repr(agen)
    try:
        module = agen.ag_frame.f_globals["__name__"]
    except (AttributeError, KeyError):
        module = "<{}>".format(agen.ag_code.co_filename)
    try:
        qualname = agen.__qualname__
    except AttributeError:
        qualname = agen.ag_code.co_name
    return f"{module}.{qualname}"
