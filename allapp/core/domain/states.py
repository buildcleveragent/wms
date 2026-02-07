# allapp/core/domain/states.py
VALID_TRANSITIONS = {
    "NEW": {"CONFIRMED","CANCELLED"},
    "CONFIRMED": {"APPROVED","CANCELLED"},
    "APPROVED": {"DONE","CANCELLED"},
}

def assert_transition(old, new):
    if new not in VALID_TRANSITIONS.get(old, set()):
        raise ValueError(f"状态不可从 {old} → {new}")
