import asyncio
from typing import Optional

from ophyd_async.core import AsyncStatus, DetectorControl, DetectorTrigger
from ophyd_async.epics.areadetector.drivers.ad_base import (
    arm_and_trigger_detector_and_check_status_pv,
)

from ..drivers.pilatus_driver import PilatusDriver, TriggerMode
from ..utils import ImageMode, stop_busy_record

TRIGGER_MODE = {
    DetectorTrigger.internal: TriggerMode.internal,
    DetectorTrigger.constant_gate: TriggerMode.ext_enable,
    DetectorTrigger.variable_gate: TriggerMode.ext_enable,
}


class PilatusController(DetectorControl):
    def __init__(self, driver: PilatusDriver) -> None:
        self.driver = driver

    def get_deadtime(self, exposure: float) -> float:
        return 0.001

    async def arm(
        self,
        mode: DetectorTrigger = DetectorTrigger.internal,
        num: int = 0,
        exposure: Optional[float] = None,
    ) -> AsyncStatus:
        await asyncio.gather(
            self.driver.trigger_mode.set(TRIGGER_MODE[mode]),
            self.driver.num_images.set(num),
            self.driver.image_mode.set(ImageMode.multiple),
        )
        return await arm_and_trigger_detector_and_check_status_pv(self.driver)

    async def disarm(self):
        await stop_busy_record(self.driver.acquire, False, timeout=1)
