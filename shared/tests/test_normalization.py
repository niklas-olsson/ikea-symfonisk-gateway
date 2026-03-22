from shared.normalization import normalize_for_comparison


def test_normalize_primitives() -> None:
    assert normalize_for_comparison(1) == 1
    assert normalize_for_comparison("string") == "string"
    assert normalize_for_comparison(True)


def test_normalize_list_sorting() -> None:
    list1 = [3, 1, 2]
    list2 = [1, 2, 3]
    assert normalize_for_comparison(list1) == normalize_for_comparison(list2)


def test_normalize_volatile_keys() -> None:
    dict1 = {"id": "1", "rssi": -50, "timestamp": 123456}
    dict2 = {"id": "1", "rssi": -60, "timestamp": 654321}
    assert normalize_for_comparison(dict1) == normalize_for_comparison(dict2)
    assert "rssi" not in normalize_for_comparison(dict1)
    assert "timestamp" not in normalize_for_comparison(dict1)


def test_normalize_list_of_dicts_sorting() -> None:
    list1 = [{"id": "b"}, {"id": "a"}]
    list2 = [{"id": "a"}, {"id": "b"}]
    assert normalize_for_comparison(list1) == normalize_for_comparison(list2)
    assert normalize_for_comparison(list1)[0]["id"] == "a"


def test_normalize_nested_structures() -> None:
    data1 = {
        "status": "ok",
        "devices": [{"mac": "00:11", "rssi": -40, "services": ["audio", "control"]}, {"mac": "22:33", "rssi": -70, "services": ["audio"]}],
        "timestamp": 100,
    }
    data2 = {
        "status": "ok",
        "devices": [{"mac": "22:33", "rssi": -75, "services": ["audio"]}, {"mac": "00:11", "rssi": -45, "services": ["control", "audio"]}],
        "timestamp": 200,
    }

    norm1 = normalize_for_comparison(data1)
    norm2 = normalize_for_comparison(data2)

    assert norm1 == norm2
    assert "rssi" not in norm1["devices"][0]
    assert norm1["devices"][0]["mac"] == "00:11"
    assert norm1["devices"][1]["services"] == ["audio"]  # services: ["audio", "control"] sorts to ["audio", "control"]?
    # Actually services are ["audio", "control"] and ["control", "audio"], they should both sort to ["audio", "control"]
    assert norm1["devices"][0]["services"] == ["audio", "control"]


def test_normalize_tuple_to_list() -> None:
    assert normalize_for_comparison((1, 2)) == [1, 2]
