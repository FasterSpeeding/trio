from collections import defaultdict
from contextlib import asynccontextmanager

import attr

from .. import _core
from .. import _util
from .. import Event

if False:
    from typing import DefaultDict, Set


@attr.s(eq=False, hash=False)
class Sequencer(metaclass=_util.Final):
    """A convenience class for forcing code in different tasks to run in an
    explicit linear order.

    Instances of this class implement a ``__call__`` method which returns an
    async context manager. The idea is that you pass a sequence number to
    ``__call__`` to say where this block of code should go in the linear
    sequence. Block 0 starts immediately, and then block N doesn't start until
    block N-1 has finished.

    Example:
      An extremely elaborate way to print the numbers 0-5, in order::

         async def worker1(seq):
             async with seq(0):
                 print(0)
             async with seq(4):
                 print(4)

         async def worker2(seq):
             async with seq(2):
                 print(2)
             async with seq(5):
                 print(5)

         async def worker3(seq):
             async with seq(1):
                 print(1)
             async with seq(3):
                 print(3)

         async def main():
            seq = trio.testing.Sequencer()
            async with trio.open_nursery() as nursery:
                nursery.start_soon(worker1, seq)
                nursery.start_soon(worker2, seq)
                nursery.start_soon(worker3, seq)

    """

    _sequence_points = attr.ib(
        factory=lambda: defaultdict(Event), init=False
    )  # type: DefaultDict[int, Event]
    _claimed = attr.ib(factory=set, init=False)  # type: Set[int]
    _broken = attr.ib(default=False, init=False)

    @asynccontextmanager
    async def __call__(self, position: int):
        if position in self._claimed:
            raise RuntimeError("Attempted to re-use sequence point {}".format(position))
        if self._broken:
            raise RuntimeError("sequence broken!")
        self._claimed.add(position)
        if position != 0:
            try:
                await self._sequence_points[position].wait()
            except _core.Cancelled:
                self._broken = True
                for event in self._sequence_points.values():
                    event.set()
                raise RuntimeError("Sequencer wait cancelled -- sequence broken")
            else:
                if self._broken:
                    raise RuntimeError("sequence broken!")
        try:
            yield
        finally:
            self._sequence_points[position + 1].set()
