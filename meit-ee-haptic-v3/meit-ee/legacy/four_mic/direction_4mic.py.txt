import math
from dataclasses import dataclass, field
import numpy as np

from gcc_phat import estimate_delay

SPEED_OF_SOUND_MPS = 343.0
MIC_RADIUS_M = 0.08

FRONT, RIGHT, BACK, LEFT = 0, 1, 2, 3
DIRECTION_NAMES = ["FRONT","FRONT_RIGHT","RIGHT","BACK_RIGHT",
                   "BACK","BACK_LEFT","LEFT","FRONT_LEFT"]

# +x = right, +y = front.  Replace with MEASURED belt coordinates.
MIC_POSITIONS_M = np.array([[ 0.0, +MIC_RADIUS_M],
                            [+MIC_RADIUS_M, 0.0],
                            [ 0.0, -MIC_RADIUS_M],
                            [-MIC_RADIUS_M, 0.0]], dtype=np.float64)

# The torso makes sound diffract AROUND the body, so the real path between
# two mics is longer than the straight line. Search window must allow for it.
TAU_MARGIN = 1.6

PAIRS_2 = [(LEFT, RIGHT), (BACK, FRONT)]
PAIRS_6 = [(0,1),(0,2),(0,3),(1,2),(1,3),(2,3)]


@dataclass
class DirectionResult:
    index: int
    name: str
    angle_deg: float
    tau_lr_s: float
    tau_fb_s: float
    confidence: float
    taus: dict = field(default_factory=dict)


def angle_to_index(angle_deg: float) -> int:
    return int(((angle_deg + 22.5) % 360.0) // 45.0)


def estimate_direction(channels, fs, mic_positions=MIC_POSITIONS_M,
                       pairs=PAIRS_2, min_conf=0.0, **kw):
    x = np.asarray(channels, dtype=np.float64)
    if x.shape[0] != 4:
        raise ValueError("channels must have shape (4, N)")
    P = np.asarray(mic_positions, dtype=np.float64)

    A, b, w, taus = [], [], [], {}
    for (i, j) in pairs:
        dij = float(np.linalg.norm(P[j] - P[i]))
        max_tau = TAU_MARGIN * dij / SPEED_OF_SOUND_MPS
        tau, conf = estimate_delay(x[i], x[j], fs, max_tau=max_tau, **kw)
        taus[(i, j)] = (tau, conf)
        if conf <= min_conf:
            continue
        A.append(P[j] - P[i])
        b.append(SPEED_OF_SOUND_MPS * tau)
        w.append(conf)

    if len(A) < 2:
        return DirectionResult(0, DIRECTION_NAMES[0], 0.0, 0.0, 0.0, 0.0, taus)

    A = np.asarray(A); b = np.asarray(b); w = np.sqrt(np.asarray(w))
    u, *_ = np.linalg.lstsq(A * w[:, None], b * w, rcond=None)

    mag = float(np.hypot(u[0], u[1]))
    angle = math.degrees(math.atan2(u[0], u[1])) % 360.0 if mag > 1e-9 else 0.0
    idx = angle_to_index(angle)

    confs = [c for (_, c) in taus.values() if c > 0]
    conf = float(np.mean(confs)) if confs else 0.0
    # |u| should be ~1 for a single far-field source; penalise inconsistency
    conf *= float(np.clip(1.0 - abs(mag - 1.0), 0.0, 1.0))

    t_lr = taus.get((LEFT, RIGHT), (0.0, 0.0))[0]
    t_fb = taus.get((BACK, FRONT), (0.0, 0.0))[0]
    return DirectionResult(idx, DIRECTION_NAMES[idx], angle, t_lr, t_fb, conf, taus)
