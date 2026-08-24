"""Exceptions raised by the import core. All are subclasses of SurveyFormatError so
callers (the QGIS-side import flow, the Processing algorithm) can catch one type and
show the message to the user - these are always about the *data*, not a plugin bug.
"""


class SurveyFormatError(Exception):
    """The zip or its session.geojson doesn't honour the handoff's data contract."""


class MediaJoinError(SurveyFormatError):
    """A non-null photo/audio property names a file the zip doesn't contain. The
    format guarantees this can't happen (handoff §2) - if it does, the zip is
    corrupt or truncated.
    """
