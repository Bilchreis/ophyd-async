"""Module which defines abstract classes to work with detectors"""
import asyncio
from abc import ABC, abstractmethod
from enum import Enum
from typing import AsyncIterator, Dict, Optional, Sequence

from bluesky.protocols import (
    Asset,
    Configurable,
    Descriptor,
    Readable,
    Reading,
    Stageable,
    Triggerable,
    WritesExternalAssets,
)

from .async_status import AsyncStatus
from .device import Device
from .signal import SignalR
from .utils import merge_gathered_dicts


class DetectorTrigger(Enum):
    #: Detector generates internal trigger for given rate
    internal = ()
    #: Expect a series of arbitrary length trigger signals
    edge_trigger = ()
    #: Expect a series of constant width external gate signals
    constant_gate = ()
    #: Expect a series of variable width external gate signals
    variable_gate = ()


class DetectorControl(ABC):
    @abstractmethod
    def get_deadtime(self, exposure: float) -> float:
        """For a given exposure, how long should the time between exposures be"""

    @abstractmethod
    async def arm(
        self,
        trigger: DetectorTrigger = DetectorTrigger.internal,
        num: int = 0,
        exposure: Optional[float] = None,
    ) -> AsyncStatus:
        """Arm the detector and return AsyncStatus.

        Awaiting the return value will wait for num frames to be written.
        """

    @abstractmethod
    async def disarm(self):
        """Disarm the detector"""


class DetectorWriter(ABC):
    @abstractmethod
    async def open(self, multiplier: int = 1) -> Dict[str, Descriptor]:
        """Open writer and wait for it to be ready for data.

        Args:
            multiplier: Each StreamDatum index corresponds to this many
                written exposures

        Returns:
            Output for ``describe()``
        """

    @abstractmethod
    async def wait_for_index(self, index: int) -> None:
        """Wait until a specific index is ready to be collected"""

    @abstractmethod
    async def get_indices_written(self) -> int:
        """Get the number of indices written"""

    @abstractmethod
    def collect_stream_docs(self, indices_written: int) -> AsyncIterator[Asset]:
        """Create Stream docs up to given number written"""

    @abstractmethod
    async def close(self) -> None:
        """Close writer and wait for it to be finished"""


class StandardDetector(
    Device,
    Stageable,
    Configurable,
    Readable,
    Triggerable,
    WritesExternalAssets,
):
    """Detector with useful default behaviour.

    Must be supplied instances of classes that inherit from DetectorControl and
    DetectorData, to dictate how the detector will be controlled (i.e. arming and
    disarming) as well as how the detector data will be written (i.e. opening and
    closing the writer, and handling data writing indices).

    NOTE: only for step-scans.
    """

    def __init__(
        self,
        control: DetectorControl,
        data: DetectorWriter,
        config_sigs: Sequence[SignalR],
        name: str = "",
    ) -> None:
        """
        Parameters
        ----------
        control:
            instance of class which inherits from :class:`DetectorControl`
        data:
            instance of class which inherits from :class:`DetectorData`
        config_sigs:
            configuration signals to be used for describing/reading the detector.
        name:
            detector name
        """
        self._control = control
        self._data = data
        self._config_sigs = config_sigs
        self._describe: Dict[str, Descriptor] = {}
        super().__init__(name)

    @property
    def control(self) -> DetectorControl:
        return self._control

    @property
    def data(self) -> DetectorWriter:
        return self._data

    @AsyncStatus.wrap
    async def stage(self) -> None:
        """Disarm the detector, stop filewriting, and open file for writing."""
        await asyncio.gather(self.data.close(), self.control.disarm())
        self._describe = await self.data.open()

    async def describe_configuration(self) -> Dict[str, Descriptor]:
        return await merge_gathered_dicts(sig.describe() for sig in self._config_sigs)

    async def read_configuration(self) -> Dict[str, Reading]:
        return await merge_gathered_dicts(sig.read() for sig in self._config_sigs)

    def describe(self) -> Dict[str, Descriptor]:
        return self._describe

    @AsyncStatus.wrap
    async def trigger(self) -> None:
        """Arm the detector and wait for it to finish."""
        indices_written = await self.data.get_indices_written()
        written_status = await self.control.arm(DetectorTrigger.internal, num=1)
        await written_status
        await self.data.wait_for_index(indices_written + 1)

    async def read(self) -> Dict[str, Reading]:
        """Unused method: will be deprecated."""
        # All data is in StreamResources, not Events, so nothing to output here
        return {}

    async def collect_asset_docs(self) -> AsyncIterator[Asset]:
        """Collect stream datum documents for all indices written."""
        indices_written = await self.data.get_indices_written()

        async for doc in self.data.collect_stream_docs(indices_written):
            yield doc
        # async for doc in self.data.collect_stream_docs(indices_written):
        #     yield doc

    @AsyncStatus.wrap
    async def unstage(self) -> None:
        """Stop data writing."""
        await self.data.close()