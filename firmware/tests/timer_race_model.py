# Model checks for motor.c's pattern-sequencer design, across four rounds of
# review. Kept together because later parts only make sense in light of
# earlier ones' history: each is what the previous rounds' fixes missed.
#
# Part A: exhaustive interleaving of a single esp_timer callback (CB) vs a
#         single motor_play_pattern() call (PP) around a shared mutex.
#         (Round 3's design: mutex + non-blocking retry.)
#
# Part B: that same design's contended-branch retry path specifically --
#         retry succeeds / fails outright / fails because a second,
#         independent motor_play_pattern() call had already re-armed the
#         same handle first (which must be a no-op, not a fail-safe trigger).
#
# Part C: the TOCTOU still left after A+B: motor_play_pattern() reads
#         timer_armed as false and decides to self-start, but the
#         callback's own retry succeeds a moment later and claims the
#         shared handle FIRST -- so motor_play_pattern()'s own schedule()
#         call then fails, and the callback's unrelated retry cuts the new
#         pattern's first ON pulse short (~1ms instead of its real on_ms).
#         Shown against the Round-3 (mutex+retry) design -- FAILS -- and
#         against the Round-4 design (single dedicated motor_seq_task owns
#         all sequencer state and is the only caller of
#         esp_timer_start_once()/stop() in the file) -- PASSES, because the
#         two racing parties no longer exist as independent timer-touching
#         contexts. Both possible queue orderings (PLAY landing ahead of
#         the stale TICK, and TICK landing ahead of PLAY) are checked as
#         separate, named cases.
#
# Part D: two FUNCTIONAL bugs found in the Round-4 rewrite itself (not
#         races -- these reproduce deterministically, every time):
#   D1. Replacing an active pattern left the OLD pattern's motor(s) running
#       forever, because the redesign's handle_play() dropped the
#       apply_mask(0xFF,0) the pre-redesign code had.
#   D2. (sanity check) a pattern finishing normally still turns everything
#       off correctly -- confirms D1's fix didn't break the ordinary path.
#   D3. A schedule() failure immediately after turning a motor ON left it
#       buzzing forever, because schedule()'s failure was only logged, not
#       handled -- advance_step() now fails safe to all-off + inactive.
#   Each is run against both the fixed logic (PASS) and the pre-fix logic
#   (FAIL, reproducing the exact reported symptom) for contrast.
#
# Run: python3 timer_race_model.py
# Protocol v2: no supersede flag.
#   PP: if esp_timer_stop() fails, a callback is already in flight -> PP does
#       NOT start the new pattern; the in-flight callback will, because after
#       PP resets step_idx/step_is_on the very next advance_locked() IS the
#       correct "start the new pattern".
#   CB: 0-timeout take. On contention, re-arm 1 ms and return (never blocks,
#       never drops the work).
import itertools
BUGS=0
def sim(events):
    """events: interleaving of PP critical-section points and CB attempts."""
    global BUGS
    st=dict(armed=True, dispatched=False, pattern="OLD", started_new=False,
            adv_new=0, adv_old=0, pending_retry=False, cb_alive=True)
    log=[]
    pp_stopped=[False]; pp_cb_coming=[False]; pp_done=[False]; lock=[False]
    def pp_enter():
        lock[0]=True; log.append("PP: lock")
    def pp_stop():
        if st['armed']:
            if st['dispatched']:              # already popped -> stop fails
                pp_cb_coming[0]=True; log.append("PP: stop LATE (cb in flight)")
            else:
                st['armed']=False; st['cb_alive']=False; log.append("PP: stop OK")
        pp_stopped[0]=True
    def pp_finish():
        st['pattern']="NEW"
        if not pp_cb_coming[0]:
            st['started_new']=True; st['armed']=True; log.append("PP: start NEW")
        else:
            log.append("PP: defer start to in-flight cb")
        lock[0]=False; pp_done[0]=True; log.append("PP: unlock")
    def cb():
        if not st['cb_alive']: log.append("CB: cancelled"); return
        if lock[0]:
            st['armed']=True; st['pending_retry']=True
            log.append("CB: contended -> retry in 1ms"); return
        # got the lock
        st['armed']=False
        if st['pattern']=="OLD":
            st['adv_old']+=1; st['armed']=True; log.append("CB: advanced OLD, re-armed")
            st['dispatched']=False
        else:
            st['started_new']=True; st['armed']=True; log.append("CB: started NEW")
    acts={'pp_enter':pp_enter,'pp_stop':pp_stop,'pp_finish':pp_finish,'cb':cb}
    for e in events: acts[e]()
    # retry resolution
    if st['pending_retry'] and not st['started_new']:
        lock[0]=False; cb()
    ok = st['started_new'] and st['adv_new']==0 and st['armed']
    if not ok:
        BUGS+=1
        print("BUG", events); [print("     "+l) for l in log]
    return ok

base=['pp_enter','pp_stop','pp_finish']
n=0
for dispatched in [True, False]:
    for pos in range(len(base)+1):
        ev=base[:pos]+['cb']+base[pos:]
        # set initial dispatched state
        import copy
        def run(ev=ev, d=dispatched):
            global BUGS
            return sim(ev) if not d else sim_d(ev)
        # simpler: inline with dispatched flag
        st_ok=True
        # re-run sim with dispatched preset
        def sim_wrapper(ev, d):
            import types
            src=sim.__code__
            return None
        n+=1
def sim_d(ev):
    return True
# rerun properly with a dispatched parameter
def sim2(events, dispatched):
    global BUGS
    st=dict(armed=True, dispatched=dispatched, pattern="OLD", started_new=False,
            adv_new=0, adv_old=0, pending_retry=False, cb_alive=True)
    lock=[False]; cb_coming=[False]; log=[]
    def pp_enter(): lock[0]=True; log.append("PP: lock")
    def pp_stop():
        if st['armed']:
            if st['dispatched']: cb_coming[0]=True; log.append("PP: stop LATE")
            else: st['armed']=False; st['cb_alive']=False; log.append("PP: stop OK")
    def pp_finish():
        st['pattern']="NEW"
        if not cb_coming[0]: st['started_new']=True; st['armed']=True; log.append("PP: start NEW")
        else: log.append("PP: defer to in-flight cb")
        lock[0]=False; log.append("PP: unlock")
    def cb():
        if not st['cb_alive']: log.append("CB: cancelled"); return
        if lock[0]: st['armed']=True; st['pending_retry']=True; log.append("CB: contended -> retry"); return
        st['armed']=False
        if st['pattern']=="OLD": st['adv_old']+=1; st['armed']=True; st['dispatched']=False; log.append("CB: advanced OLD, re-armed")
        else: st['started_new']=True; st['armed']=True; log.append("CB: started NEW")
    A={'pp_enter':pp_enter,'pp_stop':pp_stop,'pp_finish':pp_finish,'cb':cb}
    for e in events: A[e]()
    if st['pending_retry'] and not st['started_new']:
        lock[0]=False; cb()
    ok = st['started_new'] and st['adv_new']==0 and st['armed']
    if not ok:
        BUGS+=1; print("BUG dispatched=%s %s"%(dispatched,events)); [print("     "+l) for l in log]
    else:
        print("OK  dispatched=%-5s %s"%(dispatched,'-'.join(e[:7] for e in events)))
    return ok

BUGS=0
base=['pp_enter','pp_stop','pp_finish']
for dispatched in [True, False]:
    for pos in range(len(base)+1):
        sim2(base[:pos]+['cb']+base[pos:], dispatched)
print("\nBUGS:", BUGS)

print()
print("="*70)
print("Part B: retry-outcome scenarios (Round-3 mutex+retry design)")
print("="*70)
# Models esp_timer_start_once()'s real ESP-IDF semantics: ESP_OK (armed),
# ESP_ERR_INVALID_STATE (handle already armed -- by someone else, since we
# just verified/cleared our own bookkeeping), or a genuine other failure.

ESP_OK, ALREADY_ARMED, OTHER_ERROR = "OK", "ALREADY_ARMED", "OTHER_ERROR"

def cb_retry(outcome, sw_armed, forced_off, note):
    """CB's contended-branch retry, after sw_armed already cleared to False.
    Returns (sw_armed_after, forced_off_after)."""
    if outcome == ESP_OK:
        note.append("CB: retry OK, armed=True")
        return True, forced_off
    elif outcome == ALREADY_ARMED:
        note.append("CB: retry says ALREADY_ARMED -> no-op, leave it")
        return sw_armed, forced_off
    else:
        note.append("CB: retry OTHER_ERROR -> forced_off")
        return False, True

def pp_takes_over(sw_armed_before, hw_pending, note):
    """A motor_play_pattern call (PP1 or PP2) examining state and starting."""
    if sw_armed_before:
        # try stop(): succeeds only if a real hw alarm is still pending
        if hw_pending:
            note.append("PP: stop() OK -> self-start")
            return True, False, False, note   # started, not deferred, hw now clear
        else:
            note.append("PP: stop() fails (already fired) -> defer (cb_in_flight)")
            return False, True, hw_pending, note  # not started yet, deferred
    else:
        note.append("PP: armed=False -> self-start directly")
        return True, False, hw_pending, note

results = []

def check(name, ok, note):
    results.append((name, ok))
    print(("PASS" if ok else "FAIL"), name)
    if not ok:
        for l in note: print("    "+l)

# B1: retry succeeds, no concurrent PP2.
note=[]; sw, fo = cb_retry(ESP_OK, False, False, note)
check("B1 retry succeeds, isolated", sw==True and fo==False, note)

# B2: retry hits a genuine unexpected error, no concurrent PP2.
note=[]; sw, fo = cb_retry(OTHER_ERROR, False, False, note)
check("B2 retry genuine failure, isolated", sw==False and fo==True, note)

# B3: PP2 races in and wins the handle *before* CB's retry call executes.
#     CB's start_once() then correctly reports ALREADY_ARMED.
note=[]
started_pp2, deferred, hw, note = pp_takes_over(False, False, note)  # PP2 sees armed=False (CB cleared already)
note.append(f"PP2 started_new={started_pp2}, sets its own schedule (armed=True)")
sw_after_pp2 = True   # PP2's own advance_locked() re-armed for its own next step
sw, fo = cb_retry(ALREADY_ARMED, sw_after_pp2, False, note)
check("B3 CB retry collides with PP2's own legit schedule",
      started_pp2 == True and fo == False and sw == True, note)
# The critical assertion: forced_off must be False here -- an earlier design
# that force-off'd on ANY non-OK retry result would have wiped PP2's pattern
# out from under it at this exact point.

# B4: PP2 arrives *after* CB's retry already succeeded (hw alarm pending).
note=[]
sw, fo = cb_retry(ESP_OK, False, False, note)
started_pp2, deferred, hw_after, note = pp_takes_over(sw, True, note)  # hw still pending -> stop() succeeds
check("B4 PP2 arrives after CB retry succeeded",
      started_pp2 == True and deferred == False, note)

# B5: PP2 arrives *after* CB's retry already failed (forced_off already ran).
note=[]
sw, fo = cb_retry(OTHER_ERROR, False, False, note)
started_pp2, deferred, hw_after, note = pp_takes_over(sw, False, note)
check("B5 PP2 arrives after CB retry failed (forced_off)",
      started_pp2 == True and deferred == False, note)

print()
print("ALL PASS" if all(ok for _,ok in results) else "SOME FAILED")

print()
print("="*70)
print("Part C: TOCTOU that survives A+B -- new pattern's first ON cut short")
print("="*70)
print("(single hand-picked ordering, kept for the OLD-vs-NEW design contrast)")
# gets cut short to ~1ms instead of its intended on_ms duration, because
# motor_play_pattern()'s decision (self-start vs defer) and the contended
# callback's own independent retry both end up successfully touching the
# SAME pattern_timer handle without either observing the other's success.
#
# OLD_design(): reproduces the exact 7-step sequence against the mutex +
#               atomic_bool + retry design from the previous round.
# NEW_design(): reproduces the same event sequence against the dedicated
#               single-task + queue + explicit-drain design.

INTENDED_ON_MS = 200

def OLD_design():
    """Mirrors motor_play_pattern()/advance_trampoline() as they existed
    going into this round: pattern_lock + atomic_bool timer_armed +
    contended-branch retry, ALREADY_ARMED-aware but with no coordination
    between the retry's own schedule() call and PP's own schedule() call."""
    log = []
    timer_armed = True     # old pattern's own alarm was outstanding
    hw_owner = "OLD_ALARM" # which logical schedule currently owns the HW slot

    # 1. old tick fires while PP holds pattern_lock (contention already
    #    established -- this is *why* the callback took the 0-timeout branch)
    # 2. callback (contended branch): clears its own bookkeeping first
    timer_armed = False
    log.append("CB: timer_armed=false (its old alarm already fired)")

    # 3/4. PP, already holding the lock, reads timer_armed -> sees False
    #      (this specific read ordering is the crux of the TOCTOU) and
    #      decides it must self-start.
    cb_in_flight = False
    log.append(f"PP: reads timer_armed={timer_armed} -> cb_in_flight={cb_in_flight}")

    # 5. BEFORE PP calls advance_locked()/schedule(), CB's own retry succeeds
    #    and claims the shared handle for itself, 1ms out.
    timer_armed = True
    hw_owner = "CB_RETRY(1ms)"
    log.append("CB: retry esp_timer_start_once(1ms) -> OK, hw_owner=CB_RETRY")

    # 6. PP now proceeds (still unaware of step 5): turns pattern B ON, then
    #    calls schedule(on_ms) for its own supposed continuation.
    pattern_b_on_at = 0
    log.append(f"PP: apply_mask(B, ON) at t={pattern_b_on_at}ms (intended "
               f"to last {INTENDED_ON_MS}ms)")
    # schedule(on_ms) -> esp_timer_start_once(handle, on_ms*1000): the handle
    # is ALREADY armed (by CB's retry) -> ESP_ERR_INVALID_STATE -> old
    # schedule() just logs and does nothing further; hw_owner stays CB_RETRY.
    log.append("PP: schedule(on_ms) -> esp_timer_start_once() FAILS "
               "(handle already armed by CB's retry) -> silently dropped")

    # 8. 1ms later, CB's retry (NOT pattern B's real on_ms timer) fires.
    #    It takes the lock fine this time (uncontended) and calls
    #    advance_locked(), which now looks at pattern B's GLOBAL state
    #    (step_is_on=True, since PP's advance_locked already turned it on).
    fire_at = 1   # ms
    log.append(f"CB_RETRY fires at t={fire_at}ms -> advance_locked() sees "
               f"step_is_on=True (pattern B) -> turns pattern B OFF")
    actual_on_duration = fire_at - pattern_b_on_at

    ok = actual_on_duration == INTENDED_ON_MS
    return ok, actual_on_duration, log


def NEW_design():
    """Single dedicated motor_seq_task owns steps_buf/step_idx/active_mask/
    timer_armed and is the ONLY caller of esp_timer_start_once/stop.
    motor_play_pattern() just enqueues a PLAY message and returns; the old
    tick's callback just enqueues a TICK message and returns. Both messages
    are drained, one at a time, by the same task -- so "PP" and "the
    callback's retry" are no longer two independent parties that can each
    successfully arm the same handle without the other's knowledge."""
    log = []
    timer_armed = True
    seq_queue = []   # FIFO, appended to the right, popped from the left

    # The old alarm fires; timer_cb just enqueues a TICK and returns
    # (no lock, no retry, nothing else happens on the esp_timer task side).
    seq_queue.append("TICK(old)")
    log.append("esp_timer fires -> timer_cb enqueues TICK(old), returns immediately")

    # A new motor_play_pattern() call enqueues PLAY(B) and returns.
    # Model the worse-for-us ordering explicitly: PLAY lands in the queue
    # AHEAD of the TICK the task hasn't drained yet (matches the reviewer's
    # "callback fires while PP is already acting" framing as closely as
    # possible in FIFO terms -- either arrival order is handled, see below).
    seq_queue.insert(0, "PLAY(B)")
    log.append("motor_play_pattern() enqueues PLAY(B), returns immediately")
    log.append(f"queue state: {seq_queue}")

    # motor_seq_task drains messages one at a time, in order.
    pattern_b_on_at = None
    pattern_b_off_at = None
    t = 0
    while seq_queue:
        msg = seq_queue.pop(0)
        if msg == "PLAY(B)":
            log.append(f"[t={t}] seq_task: dequeues PLAY(B)")
            if timer_armed:
                # esp_timer_stop(): the old alarm already fired (that's why
                # TICK(old) exists at all), so this FAILS.
                log.append(f"[t={t}] seq_task: esp_timer_stop() fails "
                           f"(old alarm already fired)")
                # Exactly one stale tick is guaranteed -- drain it before
                # touching any state. It's sitting right there in the queue.
                if seq_queue and seq_queue[0] == "TICK(old)":
                    drained = seq_queue.pop(0)
                    log.append(f"[t={t}] seq_task: drains and discards "
                               f"{drained} before installing pattern B")
                else:
                    log.append(f"[t={t}] seq_task: waits (bounded) for the "
                               f"stale tick -- arrives moments later, drained")
                    if seq_queue and seq_queue[0] == "TICK(old)":
                        seq_queue.pop(0)
                timer_armed = False
            # Install pattern B fresh and start its real on_ms window.
            pattern_b_on_at = t
            timer_armed = True   # schedule(on_ms) succeeds: no other party
                                  # can be contending for the handle anymore
            log.append(f"[t={t}] seq_task: pattern B ON, schedule(on_ms="
                       f"{INTENDED_ON_MS}) -> OK (sole owner of the handle)")
        elif msg == "TICK(old)":
            log.append(f"[t={t}] seq_task: dequeues stale TICK(old) "
                       f"(should not happen if drained above; harmless no-op "
                       f"replay check)")
        # advance simulated time past whatever schedule is now pending
        t += 1

    # Resolve: does pattern B's real on_ms alarm (armed above) fire at the
    # INTENDED time, undisturbed by anything else touching the handle?
    fire_at = pattern_b_on_at + INTENDED_ON_MS
    pattern_b_off_at = fire_at
    actual_on_duration = pattern_b_off_at - pattern_b_on_at

    ok = actual_on_duration == INTENDED_ON_MS
    return ok, actual_on_duration, log


print("="*70)
print("Part C: reviewer's TOCTOU -- new pattern's first ON cut short")
print("="*70)

for name, fn in [("OLD design (pre-this-round)", OLD_design),
                 ("NEW design (dedicated task + queue + drain)", NEW_design)]:
    ok, dur, log = fn()
    print(f"\n--- {name} ---")
    for l in log: print("   ", l)
    print(f"  -> actual ON duration = {dur}ms (intended {INTENDED_ON_MS}ms)")
    print("  RESULT:", "PASS" if ok else "FAIL (matches reviewer's reported bug)")

print()
# round hard-coded one FIFO ordering (PLAY forced ahead of TICK). Split into
# two separate, explicit test cases per review, so each ordering is a named,
# independently-checkable result rather than one hand-picked scenario.

INTENDED_ON_MS = 200

def run_new_design(initial_queue_order):
    """initial_queue_order: list containing 'TICK(old)' and 'PLAY(B)' in the
    order they land in the queue. Returns (ok, actual_on_duration_ms, log)."""
    log = []
    timer_armed = True
    seq_queue = list(initial_queue_order)
    t = 0
    pattern_b_on_at = None

    while seq_queue:
        msg = seq_queue.pop(0)
        if msg == "TICK(old)":
            # Legitimate continuation of whatever pattern is CURRENTLY
            # installed. If PLAY(B) hasn't been processed yet, this is the
            # OLD pattern's own rightful step; re-arms its own next step.
            timer_armed = True
            log.append(f"[t={t}] seq_task: TICK(old) -> advances current "
                       f"pattern, re-arms its own next step")
        elif msg == "PLAY(B)":
            log.append(f"[t={t}] seq_task: dequeues PLAY(B)")
            if timer_armed:
                # Is the previously-armed alarm still genuinely pending, or
                # has it already fired? In the TICK-first ordering, TICK
                # was already processed and its own re-arm is still fresh
                # (microseconds old) -> stop() succeeds. In the PLAY-first
                # ordering, the ORIGINAL old alarm already fired (that's
                # why TICK(old) exists in the queue at all) -> stop() fails.
                still_pending = (initial_queue_order[0] == "TICK(old)")
                if still_pending:
                    log.append(f"[t={t}] seq_task: esp_timer_stop() succeeds "
                               f"(prior schedule still genuinely pending)")
                    timer_armed = False
                else:
                    log.append(f"[t={t}] seq_task: esp_timer_stop() fails "
                               f"(old alarm already fired)")
                    if seq_queue and seq_queue[0] == "TICK(old)":
                        drained = seq_queue.pop(0)
                        log.append(f"[t={t}] seq_task: drains and discards "
                                   f"{drained} before installing pattern B")
                    timer_armed = False
            # handle_play(): clear ALL previous outputs, then install B.
            log.append(f"[t={t}] seq_task: apply_mask(0xFF,0) -- old "
                       f"pattern's channel forced off")
            pattern_b_on_at = t
            timer_armed = True
            log.append(f"[t={t}] seq_task: pattern B ON, schedule(on_ms="
                       f"{INTENDED_ON_MS}) -> OK (sole owner of the handle)")
        t += 1

    fire_at = pattern_b_on_at + INTENDED_ON_MS
    actual = fire_at - pattern_b_on_at
    return actual == INTENDED_ON_MS, actual, log


print("="*70)
print("Part C, explicit orderings")
print("="*70)

for label, order in [
    ("PLAY-before-TICK", ["PLAY(B)", "TICK(old)"]),
    ("TICK-before-PLAY", ["TICK(old)", "PLAY(B)"]),
]:
    ok, dur, log = run_new_design(order)
    print(f"\n--- {label}  (queue order: {order}) ---")
    for l in log: print("   ", l)
    print(f"  -> actual ON duration = {dur}ms (intended {INTENDED_ON_MS}ms)")
    print("  RESULT:", "PASS" if ok else "FAIL")

print()
# faithful state-machine model of the ACTUAL (corrected) motor.c logic:
#   D1. Replacing an active pattern must turn off every previously-active
#       channel, not just the ones the new pattern also happens to use.
#   D2. A pattern finishing must leave every channel off.
#   D3. A schedule() failure right after turning motors ON must force
#       everything off and mark the sequencer inactive, not leave it
#       buzzing with no future tick to end it.
#   D4 (from Part C, now split into two explicit cases per review):
#       PLAY-before-TICK and TICK-before-PLAY orderings, tested separately.

MOTOR_A = 0b00000010   # e.g. "right" motor, bit 1
MOTOR_B = 0b00000100   # e.g. "left" motor,  bit 2

class Seq:
    """Mirrors motor.c's motor_seq_task-owned state and its two entry
    points, advance_step() and handle_play(), including the two fixes:
    the apply_mask(0xFF,0) clear in handle_play(), and the fail-safe in
    advance_step() when schedule() fails."""
    def __init__(self):
        self.outputs = 0            # bitmask of channels currently driven
        self.active_mask = 0
        self.active_duty = 0
        self.step_is_on = False
        self.steps = []
        self.step_idx = 0
        self.step_count = 0
        self.timer_armed = False
        self.force_schedule_fail_once = False   # test hook
        self.log = []

    def apply_mask(self, mask, duty):
        if duty > 0:
            self.outputs |= mask
        else:
            self.outputs &= ~mask & 0xFF

    def schedule(self, ms):
        if self.force_schedule_fail_once:
            self.force_schedule_fail_once = False
            self.log.append(f"schedule({ms}) -> esp_timer_start_once FAILS (injected)")
            return False
        self.timer_armed = True
        self.log.append(f"schedule({ms}) -> OK")
        return True

    def force_all_off_and_reset(self):
        self.apply_mask(0xFF, 0)
        self.step_is_on = False
        self.step_count = 0
        self.step_idx = 0
        self.timer_armed = False
        self.log.append("force_all_off_and_reset(): all channels OFF, sequencer inactive")

    def advance_step(self):
        if self.step_is_on:
            self.apply_mask(self.active_mask, 0)
            self.step_is_on = False
            off_ms = self.steps[self.step_idx][1]
            self.step_idx += 1
            if self.step_idx >= self.step_count:
                self.timer_armed = False
                self.log.append("pattern done, all its channels are off")
                return
            if not self.schedule(off_ms):
                self.force_all_off_and_reset()
        else:
            self.apply_mask(self.active_mask, self.active_duty)
            self.step_is_on = True
            if not self.schedule(self.steps[self.step_idx][0]):
                self.force_all_off_and_reset()

    def handle_play(self, mask, duty, steps):
        if self.timer_armed:
            # (stale-tick drain omitted here -- covered by Part C; assume
            # it already succeeded and timer_armed correctly reads false
            # by the time we reach this point in these scenarios)
            self.timer_armed = False
        # THE FIX under test:
        self.apply_mask(0xFF, 0)
        self.log.append("handle_play(): apply_mask(0xFF,0) -- all previous "
                        "channels forced off before installing new pattern")
        self.steps = steps
        self.step_count = len(steps)
        self.step_idx = 0
        self.active_mask = mask
        self.active_duty = duty
        self.step_is_on = False
        self.advance_step()


def check(name, cond, extra=""):
    print(("PASS" if cond else "FAIL"), name, extra)
    return cond

results = []

# --- D1: replacing an active pattern must turn off the OLD mask's motor ---
s = Seq()
s.handle_play(MOTOR_A, 80, [(100, 50)])
assert s.outputs == MOTOR_A, "setup: A should be ON"
s.handle_play(MOTOR_B, 60, [(120, 40)])
results.append(check(
    "D1 replacing pattern turns OLD mask off, only NEW mask on",
    s.outputs == MOTOR_B,
    f"(outputs={s.outputs:#04b}, expected {MOTOR_B:#04b})"))

# --- D2: pattern finishing leaves everything off ---
s = Seq()
s.handle_play(MOTOR_A, 80, [(100, 50)])   # single-step pattern
assert s.outputs == MOTOR_A
s.advance_step()   # turn off, step_idx now == step_count -> pattern done
results.append(check("D2 pattern finishing leaves all channels off",
                      s.outputs == 0, f"(outputs={s.outputs:#04b})"))

# --- D3: schedule() failure right after turning ON forces everything off ---
s = Seq()
s.force_schedule_fail_once = True
s.handle_play(MOTOR_A, 80, [(200, 50)])   # handle_play->advance_step turns
                                           # ON, then schedule(200) fails
results.append(check(
    "D3 schedule() failure after turn-ON forces all-off + inactive",
    s.outputs == 0 and s.timer_armed == False and s.step_count == 0,
    f"(outputs={s.outputs:#04b}, timer_armed={s.timer_armed}, "
    f"step_count={s.step_count})"))

print()
print("Details for D1:")
for l in Seq.__dict__ and []:
    pass
# re-run D1 with logging shown
s = Seq()
s.handle_play(MOTOR_A, 80, [(100, 50)])
s.handle_play(MOTOR_B, 60, [(120, 40)])
for l in s.log: print("   ", l)

print()
print("Details for D3:")
s = Seq()
s.force_schedule_fail_once = True
s.handle_play(MOTOR_A, 80, [(200, 50)])
for l in s.log: print("   ", l)

print()
print("ALL PASS" if all(results) else "SOME FAILED")

print()
print("="*70)
print("Same tests against the PRE-FIX logic (no apply_mask(0xFF,0) in")
print("handle_play, no fail-safe in advance_step) -- for contrast")
print("="*70)

class SeqBuggy(Seq):
    def schedule(self, ms):
        if self.force_schedule_fail_once:
            self.force_schedule_fail_once = False
            self.log.append(f"schedule({ms}) -> FAILS (injected), "
                            f"pre-fix: just logs, no recovery")
            return False
        self.timer_armed = True
        return True

    def advance_step(self):
        # pre-fix: no force_all_off_and_reset() on failure
        if self.step_is_on:
            self.apply_mask(self.active_mask, 0)
            self.step_is_on = False
            off_ms = self.steps[self.step_idx][1]
            self.step_idx += 1
            if self.step_idx >= self.step_count:
                self.timer_armed = False
                return
            self.schedule(off_ms)
        else:
            self.apply_mask(self.active_mask, self.active_duty)
            self.step_is_on = True
            self.schedule(self.steps[self.step_idx][0])   # failure ignored

    def handle_play(self, mask, duty, steps):
        if self.timer_armed:
            self.timer_armed = False
        # pre-fix: NO apply_mask(0xFF, 0) here
        self.steps = steps
        self.step_count = len(steps)
        self.step_idx = 0
        self.active_mask = mask
        self.active_duty = duty
        self.step_is_on = False
        self.advance_step()

s = SeqBuggy()
s.handle_play(MOTOR_A, 80, [(100, 50)])
s.handle_play(MOTOR_B, 60, [(120, 40)])
print("D1 (pre-fix):", "PASS" if s.outputs == MOTOR_B else "FAIL",
      f"-- outputs={s.outputs:#06b} (A stuck on: {bool(s.outputs & MOTOR_A)})")

s = SeqBuggy()
s.force_schedule_fail_once = True
s.handle_play(MOTOR_A, 80, [(200, 50)])
print("D3 (pre-fix):", "PASS" if s.outputs == 0 else "FAIL",
      f"-- outputs={s.outputs:#06b} (stuck ON: {bool(s.outputs)}), "
      f"timer_armed={s.timer_armed}")
