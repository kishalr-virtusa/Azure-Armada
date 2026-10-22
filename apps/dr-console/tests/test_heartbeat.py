from datetime import datetime

from dr_agent.tools.heartbeat import HeartbeatWriter


class FakeConn:
    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def cursor(self):
        return self

    def execute(self, *a):
        pass

    def commit(self):
        pass


def test_writer_starts_and_stops_cleanly():
    results = []
    w = HeartbeatWriter(lambda: FakeConn(), lambda *r: results.append(r), interval=0.05)
    w.start()
    import time
    time.sleep(0.2)
    w.stop()
    assert not w.is_alive()
    assert results and all(r[2] for r in results) and isinstance(results[0][1], datetime)
