from modules.data_integrity.duplicates import (
    calculate_sha256,
    find_exact_duplicates,
)


def test_calculate_sha256(tmp_path):
    file_path = tmp_path / "image.jpg"
    file_path.write_bytes(b"same image data")

    file_hash = calculate_sha256(str(file_path))

    assert len(file_hash) == 64
    assert all(character in "0123456789abcdef" for character in file_hash)


def test_find_exact_duplicates(tmp_path):
    images_dir = tmp_path / "images"
    images_dir.mkdir()

    first = images_dir / "image_001.jpg"
    duplicate = images_dir / "image_002.jpg"
    different = images_dir / "image_003.jpg"

    first.write_bytes(b"same image")
    duplicate.write_bytes(b"same image")
    different.write_bytes(b"different image")

    duplicates = find_exact_duplicates(str(images_dir))

    assert len(duplicates) == 1

    paths = list(duplicates.values())[0]

    assert paths == [
        "image_001.jpg",
        "image_002.jpg",
    ]


def test_no_exact_duplicates(tmp_path):
    images_dir = tmp_path / "images"
    images_dir.mkdir()

    (images_dir / "image_001.jpg").write_bytes(b"image one")
    (images_dir / "image_002.jpg").write_bytes(b"image two")

    duplicates = find_exact_duplicates(str(images_dir))

    assert duplicates == {}


def test_nested_images_are_detected(tmp_path):
    images_dir = tmp_path / "images"
    nested_dir = images_dir / "train" / "aircraft"

    nested_dir.mkdir(parents=True)

    first = nested_dir / "plane_001.jpg"
    duplicate = nested_dir / "plane_002.jpg"

    first.write_bytes(b"same aerial image")
    duplicate.write_bytes(b"same aerial image")

    duplicates = find_exact_duplicates(str(images_dir))

    assert len(duplicates) == 1

    paths = list(duplicates.values())[0]

    assert paths == [
        "train/aircraft/plane_001.jpg",
        "train/aircraft/plane_002.jpg",
    ]


def test_unsupported_files_are_ignored(tmp_path):
    images_dir = tmp_path / "images"
    images_dir.mkdir()

    (images_dir / "image_001.jpg").write_bytes(b"image")
    (images_dir / "notes.txt").write_bytes(b"image")

    duplicates = find_exact_duplicates(str(images_dir))

    assert duplicates == {}