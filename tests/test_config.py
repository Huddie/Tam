import json
import textwrap

import pytest

from tam.config import Config


def test_yaml_dot_access(tmp_path):
    path = tmp_path / "cfg.yaml"
    path.write_text(
        textwrap.dedent(
            """
            foo:
              bar: 42
              nested:
                baz: "hello"
            items:
              - a: 1
              - a: 2
            """
        )
    )
    cfg = Config(path)
    assert cfg.foo.bar == 42
    assert cfg.foo.nested.baz == "hello"
    assert cfg.items[0].a == 1
    assert cfg.items[1].a == 2


def test_json_dot_access(tmp_path):
    path = tmp_path / "cfg.json"
    path.write_text(json.dumps({"foo": {"bar": 7}}))
    cfg = Config(path)
    assert cfg.foo.bar == 7


def test_missing_key_raises_attribute_error(tmp_path):
    path = tmp_path / "cfg.yaml"
    path.write_text("foo:\n  bar: 1\n")
    cfg = Config(path)
    with pytest.raises(AttributeError):
        _ = cfg.foo.missing


def test_config_key_named_like_a_helper_method_wins(tmp_path):
    path = tmp_path / "cfg.yaml"
    path.write_text("items: [1, 2, 3]\nkeys: x\nget: y\n")
    cfg = Config(path)
    assert list(cfg.items) == [1, 2, 3]
    assert cfg.keys == "x"
    assert cfg.get == "y"


def test_unsupported_extension_raises(tmp_path):
    path = tmp_path / "cfg.toml"
    path.write_text("foo = 1")
    with pytest.raises(ValueError):
        Config(path)


def test_calling_section_with_a_class_builds_new_instance(tmp_path):
    path = tmp_path / "cfg.yaml"
    path.write_text("foo:\n  bar: 1\n  baz: two\n")
    cfg = Config(path)

    class Target:
        pass

    obj = cfg.foo(Target)
    assert isinstance(obj, Target)
    assert obj.bar == 1
    assert obj.baz == "two"


def test_calling_section_with_an_instance_mutates_in_place(tmp_path):
    path = tmp_path / "cfg.yaml"
    path.write_text("foo:\n  bar: 1\n  baz: two\n")
    cfg = Config(path)

    class Target:
        pass

    obj = Target()
    result = cfg.foo(obj)
    assert result is obj
    assert obj.bar == 1
    assert obj.baz == "two"


def test_setdefault_sets_when_absent_and_leaves_existing_value_alone(tmp_path):
    path = tmp_path / "cfg.yaml"
    path.write_text("foo:\n  bar: 1\n")
    cfg = Config(path)

    assert cfg.foo.setdefault("baz", 99) == 99
    assert cfg.foo.baz == 99
    assert cfg.foo.setdefault("bar", 12345) == 1  # already set -- untouched
    assert cfg.foo.bar == 1
