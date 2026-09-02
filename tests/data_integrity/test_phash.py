from PIL import Image

from modules.data_integrity.phash import (
    calculate_phash,
    create_near_duplicate_findings,
    find_near_duplicates,
    phash_confidence,
    phash_distance,
)


def test_phash_same_image(tmp_path):
    image_path = tmp_path / "image.jpg"

    image = Image.new("RGB", (100, 100), "white")
    image.save(image_path)

    hash_a = calculate_phash(str(image_path))
    hash_b = calculate_phash(str(image_path))

    assert hash_a == hash_b
    assert phash_distance(hash_a, hash_b) == 0


def test_phash_confidence():
    assert phash_confidence(0) == 1.0
    assert phash_confidence(10) == 0.84
    assert phash_confidence(64) == 0.0


def test_find_near_duplicates(tmp_path):
    images_dir = tmp_path / "images"
    images_dir.mkdir()

    first = images_dir / "image_001.jpg"
    second = images_dir / "image_002.jpg"

    image = Image.new("RGB", (100, 100), "white")
    image.save(first)
    image.save(second)

    matches = find_near_duplicates(str(images_dir))

    assert len(matches) == 1
    assert matches[0]["phash_distance"] == 0


def test_create_near_duplicate_finding():
    matches = [
        {
            "image_a": "images/a.jpg",
            "image_b": "images/b.jpg",
            "phash_distance": 10,
        }
    ]

    findings = create_near_duplicate_findings(matches)

    assert len(findings) == 1

    finding = findings[0]

    assert finding["finding_id"].startswith("F-DATA-")
    assert finding["module"] == "dataset_integrity"
    assert finding["asset_type"] == "sample"
    assert finding["asset_id"] == "images/a.jpg"
    assert finding["category"] == "NEAR_DUPLICATE"
    assert finding["severity"] == "MEDIUM"
    assert finding["confidence"] == 0.84
    assert finding["recommendation"] == "REVIEW"

    assert "phash_distance=10" in finding["evidence"]
