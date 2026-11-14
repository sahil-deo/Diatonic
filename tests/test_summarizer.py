from diatonic.voice.summarizer import clean_for_speech, should_speak

R = {"tools": True, "approvals": True, "errors": True, "min_seconds": 10}


def test_clean():
    assert clean_for_speech("NONE") is None and clean_for_speech("") is None
    assert clean_for_speech("Done: see https://a.b/c now") == "Done: see now"
    long = " ".join(["word"] * 60)
    assert len(clean_for_speech(long).split()) <= 30


def test_rules():
    k = dict(used_tools=False, had_approval=False, had_error=False, seconds=1)
    assert not should_speak(R, **k)
    assert should_speak(R, **{**k, "used_tools": True})
    assert should_speak(R, **{**k, "had_error": True})
    assert should_speak(R, **{**k, "seconds": 11})
    assert not should_speak({**R, "tools": False}, **{**k, "used_tools": True})
