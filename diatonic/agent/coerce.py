"""Forgive harmless argument mistakes instead of bouncing them back to a small model.

A 4B model asks for limit=50 where the maximum is 30, sends "5" for an integer, or "High" for an enum.
Rejecting those costs a whole extra model call, every chat. Fix them silently; only report real clamps.
"""
from __future__ import annotations


def coerce_args(args: dict, schema: dict) -> tuple[dict, list[str]]:
    """Returns (fixed_args, clamp_notes). Unknown or un-fixable values are left for validation to report."""
    props = schema.get("properties", {})
    out, notes = dict(args), []
    for k, spec in props.items():
        if k not in out:
            continue
        v, t = out[k], spec.get("type")
        if t in ("integer", "number"):
            if isinstance(v, bool):
                continue
            if isinstance(v, str):
                try:
                    v = float(v.strip())
                except ValueError:
                    continue
            if not isinstance(v, (int, float)):
                continue
            if t == "integer":
                v = int(round(v))
            elif float(v).is_integer():
                v = int(v)
            fixed = v
            lo, hi = spec.get("minimum"), spec.get("maximum")
            if lo is not None and fixed < lo:
                fixed = lo
            if hi is not None and fixed > hi:
                fixed = hi
            if fixed != v:
                notes.append(f"{k} {v} became {fixed} (allowed {lo if lo is not None else '…'}–{hi if hi is not None else '…'})")
            out[k] = fixed
        elif t == "boolean" and isinstance(v, str):
            low = v.strip().lower()
            if low in ("true", "yes", "1"):
                out[k] = True
            elif low in ("false", "no", "0"):
                out[k] = False
        elif t == "string" and isinstance(v, (int, float)) and not isinstance(v, bool):
            out[k] = str(v)
        elif t == "array" and isinstance(v, str):
            out[k] = [x.strip() for x in v.split(",") if x.strip()]
        if "enum" in spec and isinstance(out[k], str) and out[k] not in spec["enum"]:
            match = next((e for e in spec["enum"] if isinstance(e, str) and e.lower() == out[k].strip().lower()), None)
            if match is not None:
                out[k] = match
    return out, notes
