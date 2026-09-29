import asyncio

import laptop.ble_receiver as br


def test_run_forever_retries_after_error_and_disconnect(monkeypatch):
    calls = []

    async def fake_run(mock_ai, scan_timeout):
        calls.append(len(calls))
        if len(calls) == 1:
            raise RuntimeError("simulated connect failure")
        if len(calls) == 3:
            raise asyncio.CancelledError  # stop the endless loop for the test
        return None                       # simulated clean disconnect

    monkeypatch.setattr(br, "run", fake_run)

    async def scenario():
        try:
            await br.run_forever(True, 1.0, once=False, retry_delay=0)
        except asyncio.CancelledError:
            pass

    asyncio.run(scenario())
    assert len(calls) == 3   # error -> retried, disconnect -> retried


def test_run_forever_once_exits_after_first_session(monkeypatch):
    calls = []

    async def fake_run(mock_ai, scan_timeout):
        calls.append(1)

    monkeypatch.setattr(br, "run", fake_run)
    asyncio.run(br.run_forever(True, 1.0, once=True, retry_delay=0))
    assert calls == [1]
