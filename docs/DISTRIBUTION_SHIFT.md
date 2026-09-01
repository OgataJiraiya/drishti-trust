# Distribution Shift — D1 image-window profiling

D1 answers only: **what exactly are the reference and current distributions that
later milestones will compare?** It creates deterministic image-window profile
evidence. It makes no drift, risk, severity, disposition, trust, model-performance,
or deployment-safety judgment and emits no Frozen Finding.

An `ImageWindowProfiler` profiles a directory as `REFERENCE` or `CURRENT` using
identical processing. A reference has `DESIGNATED_REFERENCE` semantics: designation
does not establish authenticity, approval, trust, or safety. Role metadata is not
part of content profile identity, so identical reference/current content has the
same `drift-profile:sha256:...` identity.

## Safe intake and bounds

Traversal is lexicographically sorted, confined to the supplied directory, does not
follow symlinks, ignores hidden entries and unsupported suffixes, accepts regular
files only, resolves paths beneath the resolved root, and supports JPEG, PNG, BMP,
TIFF, and WEBP decoding through Pillow. Extension is only an intake filter; decoded
format is authoritative. Hashing uses streaming SHA-256 in 1 MiB chunks and sample
identity is content-based, never filename-based.

Defaults bound files to 64 MiB, decoded images to 50 million pixels, windows to
10,000 selected samples, failure examples to 20, relative paths to 256 characters,
format categories to 16, and histograms to 256 bins. Size is checked before decode;
dimensions are checked before RGB conversion. Multiframe images are rejected.
Selection is lexicographic first-N and truncation produces `PARTIAL`. Each supplied
file is an observation, including byte-identical duplicates. Duplicate canonical
commitment entries preserve multiplicity without absolute paths.

`COMPLETE` means every eligible sample was profiled. `PARTIAL` means useful evidence
exists but failures or truncation occurred. `UNAVAILABLE` means no usable sample was
available. These describe coverage, not safety.

## Feature definitions and aggregation

Images are converted deterministically to RGB; alpha is ignored rather than
composited. Original mode is grouped as `GRAYSCALE`, `RGB`, or `RGBA`. Unknown modes
are rejected. EXIF is neither read into evidence nor serialized.

- Luminance: `0.2126 R + 0.7152 G + 0.0722 B`, with RGB normalized to `[0,1]`.
- Brightness: population mean of normalized luminance.
- Contrast: population standard deviation of normalized luminance.
- Sharpness: population variance of concatenated horizontal and vertical first
  differences. It is a relative sharpness proxy, not ground-truth blur quality;
  a 1×1 image has explicitly defined zero support.
- Entropy: base-2 Shannon entropy of the fixed luminance histogram; empty bins are
  excluded.
- Saturation: mean `(max(R,G,B)-min(R,G,B))/max(R,G,B)`, with black pixels and
  grayscale deterministically zero.
- Dimensions: width, height, pixel count, and width/height aspect ratio.

Each continuous feature stores count, min, max, mean, population standard deviation,
median, and q05/q25/q75/q95 using NumPy's `method="linear"`. Brightness and saturation
also store fixed `[0,1]` histograms for D2 readiness. Other unbounded features retain
summaries rather than incomparable window-relative histograms.

The sample-set commitment hashes sorted canonical entries containing content SHA-256,
byte size, decoded format, width, and height. The profile ID commits to schema version,
material limits, counts, commitment, aggregate summaries/histograms, status, failure
counts, and limitations. It excludes role, timestamps, UUIDs, host/user identity,
temporary paths, and filenames. Serialization reuses the existing read-only canonical
JSON helper and every public profile passes `json.dumps(..., allow_nan=False)`.

Failures are mapped to bounded codes rather than decoder exception text. Only bounded,
normalized relative paths may appear in examples. Profiles never contain raw bytes,
pixel/luminance arrays, embeddings, EXIF/GPS, absolute paths, or decoder tracebacks.

## Offline boundary and limitations

D1 uses only local Pillow and NumPy processing. It performs no model inference,
download, network request, external command, or cloud operation.

- A filesystem scan is not an atomic snapshot; obvious size/mtime changes are detected.
- Image-decoder vulnerabilities cannot be completely eliminated.
- Summary statistics discard distribution detail.
- Sample truncation produces partial evidence.
- Handcrafted quality features do not capture semantic shift.
- Reference designation does not establish authenticity.
- D1 makes no drift or model-performance conclusion.
- Profile equality does not prove deployment safety.

D2 will compare compatible reference/current D1 evidence using separately specified
statistical and image-quality drift semantics. No D2 thresholds or decisions exist here.
