from subtrans.mkv import Track

CODECS = {"text": "S_TEXT/UTF8", "pgs": "S_HDMV/PGS", "vobsub": "S_VOBSUB"}


def track(id: int, language: str, kind: str = "text", forced: bool = False) -> Track:
    return Track(id=id, codec=CODECS[kind], language=language, name="", kind=kind, forced=forced)
