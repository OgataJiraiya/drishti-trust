class ModelInspectionError(RuntimeError):
    """Bounded base error for hostile artifact intake."""


class InvalidArtifactError(ModelInspectionError): pass
class UnsupportedFormatError(ModelInspectionError): pass
class OnnxInspectionError(ModelInspectionError): pass
class ResourceLimitError(ModelInspectionError): pass
