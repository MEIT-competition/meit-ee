from laptop.protocol import DIRECTION_NAMES
import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

_state = {"seq": 0, "sound_class": None, "direction": -1, "intensity": 0}
_lock = threading.Lock()
_HTML = Path(__file__).with_name("display.html")


def show(sound_class=None, direction=-1, intensity=0):
    """판단 결과를 화면에 반영. 알림 없으면 인자 없이 호출."""
    if direction not in DIRECTION_NAMES:
        direction = -1
    with _lock:
        _state["seq"] += 1
        _state["sound_class"] = sound_class
        _state["direction"] = direction
        _state["direction_name"] = DIRECTION_NAMES.get(direction, "UNKNOWN")
        _state["intensity"] = intensity


class _Handler(BaseHTTPRequestHandler):
    def do_GET(self):
        if self.path.startswith("/state"):
            with _lock:
                body = json.dumps(_state).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Cache-Control", "no-store")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            return

        body = _HTML.read_bytes()
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *args):
        pass          # 터미널 지저분해지지 않게


def start(port=8000):
    try:
        srv = ThreadingHTTPServer(("127.0.0.1", port), _Handler)
    except OSError as exc:
        print(f"[화면] 포트 {port} 을 쓸 수 없어 화면 없이 진행합니다 ({exc})")
        return
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    print(f"[화면] http://localhost:{port} 을 브라우저에서 여세요")