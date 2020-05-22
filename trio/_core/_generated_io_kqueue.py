# ***********************************************************
# ******* WARNING: AUTOGENERATED! ALL EDITS WILL BE LOST ******
# *************************************************************
from ._run import GLOBAL_RUN_CONTEXT, _NO_SEND
from ._ki import LOCALS_KEY_KI_PROTECTION_ENABLED

# fmt: off


def current_kqueue():
    locals()[LOCALS_KEY_KI_PROTECTION_ENABLED] = True
    try:
        return GLOBAL_RUN_CONTEXT.runner.io_manager.current_kqueue()
    except AttributeError:
        raise RuntimeError("must be called from async context")


def monitor_kevent(ident, filter):
    locals()[LOCALS_KEY_KI_PROTECTION_ENABLED] = True
    try:
        return GLOBAL_RUN_CONTEXT.runner.io_manager.monitor_kevent(ident, filter)
    except AttributeError:
        raise RuntimeError("must be called from async context")


async def wait_kevent(ident, filter, abort_func):
    locals()[LOCALS_KEY_KI_PROTECTION_ENABLED] = True
    try:
        return await GLOBAL_RUN_CONTEXT.runner.io_manager.wait_kevent(ident, filter, abort_func)
    except AttributeError:
        raise RuntimeError("must be called from async context")


async def wait_readable(fd):
    locals()[LOCALS_KEY_KI_PROTECTION_ENABLED] = True
    try:
        return await GLOBAL_RUN_CONTEXT.runner.io_manager.wait_readable(fd)
    except AttributeError:
        raise RuntimeError("must be called from async context")


async def wait_writable(fd):
    locals()[LOCALS_KEY_KI_PROTECTION_ENABLED] = True
    try:
        return await GLOBAL_RUN_CONTEXT.runner.io_manager.wait_writable(fd)
    except AttributeError:
        raise RuntimeError("must be called from async context")


def notify_closing(fd):
    locals()[LOCALS_KEY_KI_PROTECTION_ENABLED] = True
    try:
        return GLOBAL_RUN_CONTEXT.runner.io_manager.notify_closing(fd)
    except AttributeError:
        raise RuntimeError("must be called from async context")


# fmt: on
