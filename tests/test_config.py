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


def test_base_merges_and_leaf_overrides_win(tmp_path):
    (tmp_path / "base.yaml").write_text("a: 1\nnested:\n  x: 10\n  y: 20\n")
    (tmp_path / "leaf.yaml").write_text("base: base.yaml\nnested:\n  y: 99\n  z: 30\n")

    cfg = Config(tmp_path / "leaf.yaml")

    assert cfg.a == 1  # inherited untouched
    assert cfg.nested.x == 10  # inherited untouched
    assert cfg.nested.y == 99  # leaf overrides base
    assert cfg.nested.z == 30  # leaf-only key


def test_base_list_merges_left_to_right(tmp_path):
    (tmp_path / "a.yaml").write_text("x: 1\ny: 1\n")
    (tmp_path / "b.yaml").write_text("y: 2\nz: 2\n")
    (tmp_path / "leaf.yaml").write_text("base: [a.yaml, b.yaml]\n")

    cfg = Config(tmp_path / "leaf.yaml")

    assert cfg.x == 1
    assert cfg.y == 2  # b.yaml (later in the list) wins over a.yaml
    assert cfg.z == 2


def test_base_chain_resolves_recursively(tmp_path):
    (tmp_path / "grandparent.yaml").write_text("a: 1\n")
    (tmp_path / "parent.yaml").write_text("base: grandparent.yaml\nb: 2\n")
    (tmp_path / "leaf.yaml").write_text("base: parent.yaml\nc: 3\n")

    cfg = Config(tmp_path / "leaf.yaml")

    assert (cfg.a, cfg.b, cfg.c) == (1, 2, 3)


def test_circular_base_reference_raises(tmp_path):
    (tmp_path / "a.yaml").write_text("base: b.yaml\n")
    (tmp_path / "b.yaml").write_text("base: a.yaml\n")

    with pytest.raises(ValueError, match="circular"):
        Config(tmp_path / "a.yaml")


def test_env_var_expansion(tmp_path, monkeypatch):
    monkeypatch.setenv("TAM_TEST_TOKEN", "secret123")
    path = tmp_path / "cfg.yaml"
    path.write_text('token: "${TAM_TEST_TOKEN}"\nmissing: "${TAM_TEST_UNSET_VAR}"\n')

    cfg = Config(path)

    assert cfg.token == "secret123"
    assert cfg.missing == ""  # unset env var expands to empty string, not left as-is


def test_include_whole_file(tmp_path):
    (tmp_path / "shared.yaml").write_text("window: 20\nqty: 5\n")
    path = tmp_path / "cfg.yaml"
    path.write_text("params: << shared.yaml\n")

    cfg = Config(path)

    assert cfg.params.window == 20
    assert cfg.params.qty == 5


def test_include_section(tmp_path):
    (tmp_path / "shared.yaml").write_text("signals:\n  sma:\n    window: 20\n  rsi:\n    period: 14\n")
    path = tmp_path / "cfg.yaml"
    path.write_text("sma_config: << shared.yaml#signals.sma\n")

    cfg = Config(path)

    assert cfg.sma_config.window == 20


def test_include_missing_section_raises(tmp_path):
    (tmp_path / "shared.yaml").write_text("signals:\n  sma:\n    window: 20\n")
    path = tmp_path / "cfg.yaml"
    path.write_text("x: << shared.yaml#signals.missing\n")

    with pytest.raises(ValueError, match="missing"):
        Config(path)


def test_vars_string_interpolation_and_whole_token_type_preservation(tmp_path):
    path = tmp_path / "cfg.yaml"
    path.write_text(
        """
vars:
  ticker: QQQ
  windows: [10, 50, 200]

label: "signal for {{vars.ticker}}"
tickers: "{{vars.windows}}"
"""
    )

    cfg = Config(path)

    assert cfg.label == "signal for QQQ"
    assert cfg.tickers == [10, 50, 200]  # whole-token reference keeps its real type (a list, not a string)
    assert not hasattr(cfg, "vars")  # the vars: block itself is dropped from the resolved config


def test_vars_can_reference_other_vars_regardless_of_declaration_order(tmp_path):
    path = tmp_path / "cfg.yaml"
    path.write_text(
        """
vars:
  full_name: "{{vars.first}} {{vars.last}}"
  first: Ada
  last: Lovelace

greeting: "hello {{vars.full_name}}"
"""
    )

    cfg = Config(path)

    assert cfg.greeting == "hello Ada Lovelace"


def test_vars_resolved_once_after_full_base_chain_not_per_base_file(tmp_path):
    # base.yaml references {{vars.env}} but doesn't define it -- the leaf
    # config supplies it. Resolving vars per-file (instead of once, after the
    # full chain merges) would raise here on the base file alone.
    (tmp_path / "base.yaml").write_text('path: "/data/{{vars.env}}/set"\n')
    (tmp_path / "leaf.yaml").write_text("base: base.yaml\nvars:\n  env: prod\n")

    cfg = Config(tmp_path / "leaf.yaml")

    assert cfg.path == "/data/prod/set"


def test_missing_var_raises_clear_error(tmp_path):
    path = tmp_path / "cfg.yaml"
    path.write_text('x: "{{vars.does_not_exist}}"\n')

    with pytest.raises(ValueError, match="does_not_exist"):
        Config(path)
