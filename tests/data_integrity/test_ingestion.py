from modules.data_integrity.ingestion import create_sample


def test_create_sample():
    sample = create_sample(
        sample_id="img_0042",
        image_path="images/img_0042.jpg",
        labels=["car"],
        contributor_id="CONTRIB-C",
        batch_id="BATCH-017",
    )

    assert sample["sample_id"] == "img_0042"
    assert sample["image_path"] == "images/img_0042.jpg"
    assert sample["labels"] == ["car"]
    assert sample["contributor_id"] == "CONTRIB-C"
    assert sample["batch_id"] == "BATCH-017"