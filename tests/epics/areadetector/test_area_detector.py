"""Integration tests for a StandardDetector using a HDFWriter and ADController.

Until we have SimController and SimWriter, this should belond in areadetector tests.
Once those have been implemented can move this into core tests.
"""


import time
from pathlib import Path
from typing import List, cast

import bluesky.plan_stubs as bps
import pytest
from bluesky import RunEngine
from bluesky.utils import new_uid

from ophyd_async.core import DeviceCollector, StandardDetector, StaticDirectoryProvider
from ophyd_async.core.signal import set_sim_value
from ophyd_async.epics.areadetector.controllers import ADController
from ophyd_async.epics.areadetector.drivers import ADDriver, ADDriverShapeProvider
from ophyd_async.epics.areadetector.utils import FileWriteMode, ImageMode
from ophyd_async.epics.areadetector.writers import HDFWriter, NDFileHDF

CURRENT_DIRECTORY = Path(__file__).parent


async def make_detector(prefix="", name="test"):
    dp = StaticDirectoryProvider(CURRENT_DIRECTORY, f"test-{new_uid()}")

    async with DeviceCollector(sim=True):
        drv = ADDriver(prefix=f"{prefix}:DET:")
        hdf = NDFileHDF(f"{prefix}:HDF:")
        writer = HDFWriter(hdf, dp, lambda: name, ADDriverShapeProvider(drv))
        det = StandardDetector(
            ADController(drv), writer, config_sigs=[drv.acquire_time]
        )

    det.set_name(name)
    return det


def count_sim(dets: List[StandardDetector], times: int = 1):
    """Test plan to do the equivalent of bp.count for a sim detector."""

    yield from bps.stage_all(*dets)
    yield from bps.open_run()
    yield from bps.declare_stream(*dets, name="primary", collect=False)
    for _ in range(times):
        read_values = {}
        for det in dets:
            read_values[det] = yield from bps.rd(
                cast(HDFWriter, det.data).hdf.num_captured
            )

        for det in dets:
            yield from bps.trigger(det, wait=False, group="wait_for_trigger")

        yield from bps.sleep(0.001)
        [
            set_sim_value(
                cast(HDFWriter, det.data).hdf.num_captured, read_values[det] + 1
            )
            for det in dets
        ]

        yield from bps.wait(group="wait_for_trigger")
        yield from bps.create()

        for det in dets:
            yield from bps.read(det)

        yield from bps.save()

    yield from bps.close_run()
    yield from bps.unstage_all(*dets)


@pytest.fixture
async def single_detector(RE: RunEngine) -> StandardDetector:
    detector = await make_detector(prefix="TEST")

    set_sim_value(cast(ADController, detector.control).driver.array_size_x, 10)
    set_sim_value(cast(ADController, detector.control).driver.array_size_y, 20)
    return detector


@pytest.fixture
async def two_detectors():
    deta = await make_detector(prefix="PREFIX1", name="testa")
    detb = await make_detector(prefix="PREFIX2", name="testb")

    # Simulate backend IOCs being in slightly different states
    for i, det in enumerate((deta, detb)):
        controller = cast(ADController, det.control)
        writer = cast(HDFWriter, det.data)

        set_sim_value(controller.driver.acquire_time, 0.8 + i)
        set_sim_value(controller.driver.image_mode, ImageMode.continuous)
        set_sim_value(writer.hdf.num_capture, 1000)
        set_sim_value(writer.hdf.num_captured, 0)
        set_sim_value(controller.driver.array_size_x, 1024 + i)
        set_sim_value(controller.driver.array_size_y, 768 + i)
    yield deta, detb


async def test_two_detectors_step(
    two_detectors: List[StandardDetector],
    RE: RunEngine,
):
    names = []
    docs = []
    RE.subscribe(lambda name, _: names.append(name))
    RE.subscribe(lambda _, doc: docs.append(doc))

    RE(count_sim(two_detectors, times=1))

    controller_a = cast(ADController, two_detectors[0].control)
    writer_a = cast(HDFWriter, two_detectors[0].data)
    writer_b = cast(HDFWriter, two_detectors[1].data)

    drv = controller_a.driver
    assert 1 == await drv.acquire.get_value()
    assert ImageMode.single == await drv.image_mode.get_value()

    hdfb = writer_b.hdf
    assert True is await hdfb.lazy_open.get_value()
    assert True is await hdfb.swmr_mode.get_value()
    assert 0 == await hdfb.num_capture.get_value()
    assert FileWriteMode.stream == await hdfb.file_write_mode.get_value()

    assert names == [
        "start",
        "descriptor",
        "stream_resource",
        "stream_datum",
        "stream_resource",
        "stream_datum",
        "event",
        "stop",
    ]
    info_a = writer_a._directory_provider()
    info_b = writer_b._directory_provider()

    assert await writer_a.hdf.file_path.get_value() == info_a.directory_path
    assert (await writer_a.hdf.file_name.get_value()).startswith(info_a.filename_prefix)

    assert await writer_b.hdf.file_path.get_value() == info_b.directory_path
    assert (await writer_b.hdf.file_name.get_value()).startswith(info_b.filename_prefix)

    _, descriptor, sra, sda, srb, sdb, event, _ = docs
    assert descriptor["configuration"]["testa"]["data"]["drv-acquire_time"] == 0.8
    assert descriptor["configuration"]["testb"]["data"]["drv-acquire_time"] == 1.8
    assert descriptor["data_keys"]["testa"]["shape"] == (768, 1024)
    assert descriptor["data_keys"]["testb"]["shape"] == (769, 1025)
    assert sda["stream_resource"] == sra["uid"]
    assert sdb["stream_resource"] == srb["uid"]
    assert event["data"] == {}


async def test_detector_writes_to_file(
    RE: RunEngine, single_detector: StandardDetector
):
    names = []
    docs = []
    RE.subscribe(lambda name, _: names.append(name))
    RE.subscribe(lambda _, doc: docs.append(doc))
    RE(count_sim([single_detector], times=3))

    assert (
        await cast(HDFWriter, single_detector.data).hdf.file_path.get_value()
        == CURRENT_DIRECTORY
    )

    descriptor_index = names.index("descriptor")

    assert docs[descriptor_index].get("data_keys").get("test").get("shape") == (20, 10)
    assert names == [
        "start",
        "descriptor",
        "stream_resource",
        "stream_datum",
        "event",
        "stream_datum",
        "event",
        "stream_datum",
        "event",
        "stop",
    ]


async def test_read_and_describe_detector(single_detector: StandardDetector):
    describe = await single_detector.describe_configuration()
    read = await single_detector.read_configuration()

    assert describe == {
        "drv-acquire_time": {
            "source": "sim://TEST:DET:AcquireTime_RBV",
            "dtype": "number",
            "shape": [],
        }
    }
    assert read == {
        "drv-acquire_time": {
            "value": 0.0,
            "timestamp": pytest.approx(time.monotonic(), rel=1e-2),
            "alarm_severity": 0,
        }
    }


async def test_read_returns_nothing(single_detector: StandardDetector):
    assert await single_detector.read() == {}


async def test_trigger_logic():
    """I want this test to check that when StandardDetector.trigger is called:

    1. the detector.control is armed, and that starts the acquisition so that,
    2. The detector.data.hdf.num_captured is 1

    Probably the best thing to do here is mock the detector.control.driver and
    detector.data.hdf. Then, mock out set_and_wait_for_value in
    ophyd_async.epics.areadetector.controllers.standard_controller.ADController
    so that, as well as setting detector.control.driver.acquire to True, it sets
    detector.data.hdf.num_captured to 1, using set_sim_value
    """
    ...