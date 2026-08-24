import pytest

from tam.registry import Registry, RunRegistry


def test_register_returns_class_unchanged_and_makes_it_creatable():
    class _BaseRegisterReturn:
        pass

    class _ImplRegisterReturn(_BaseRegisterReturn):
        pass

    decorated = Registry.register(_BaseRegisterReturn, "impl")(_ImplRegisterReturn)

    assert decorated is _ImplRegisterReturn
    assert isinstance(Registry.create(_BaseRegisterReturn, "impl"), _ImplRegisterReturn)
    assert isinstance(Registry.get(_BaseRegisterReturn, "impl"), _ImplRegisterReturn)


def test_create_returns_fresh_instance_with_args_passed_through():
    class _BaseCreateFresh:
        pass

    @Registry.register(_BaseCreateFresh, "thing")
    class _ImplCreateFresh(_BaseCreateFresh):
        def __init__(self, value):
            self.value = value

    first = Registry.create(_BaseCreateFresh, "thing", "hello")
    second = Registry.create(_BaseCreateFresh, "thing", value="world")

    assert first is not second
    assert first.value == "hello"
    assert second.value == "world"


def test_get_returns_cached_singleton_across_calls():
    class _BaseGetSingleton:
        pass

    @Registry.register(_BaseGetSingleton, "thing")
    class _ImplGetSingleton(_BaseGetSingleton):
        pass

    first = Registry.get(_BaseGetSingleton, "thing")
    second = Registry.get(_BaseGetSingleton, "thing")

    assert first is second


def test_subscript_access_matches_get_singleton():
    class _BaseSubscript:
        pass

    @Registry.register(_BaseSubscript, "thing")
    class _ImplSubscript(_BaseSubscript):
        pass

    assert Registry[_BaseSubscript, "thing"] is Registry.get(_BaseSubscript, "thing")


def test_register_duplicate_key_raises_value_error():
    class _BaseDuplicate:
        pass

    @Registry.register(_BaseDuplicate, "thing")
    class _ImplDuplicateOne(_BaseDuplicate):
        pass

    with pytest.raises(ValueError):
        @Registry.register(_BaseDuplicate, "thing")
        class _ImplDuplicateTwo(_BaseDuplicate):
            pass


def test_create_unregistered_name_raises_key_error_with_name_in_message():
    class _BaseUnregisteredCreate:
        pass

    with pytest.raises(KeyError, match="missing"):
        Registry.create(_BaseUnregisteredCreate, "missing")


def test_get_unregistered_name_raises_key_error_with_name_in_message():
    class _BaseUnregisteredGet:
        pass

    with pytest.raises(KeyError, match="missing"):
        Registry.get(_BaseUnregisteredGet, "missing")


def test_names_returns_sorted_names_scoped_to_base_type():
    class _BaseNamesA:
        pass

    class _BaseNamesB:
        pass

    @Registry.register(_BaseNamesA, "zebra")
    class _ImplNamesAZebra(_BaseNamesA):
        pass

    @Registry.register(_BaseNamesA, "alpha")
    class _ImplNamesAAlpha(_BaseNamesA):
        pass

    @Registry.register(_BaseNamesB, "unrelated")
    class _ImplNamesBUnrelated(_BaseNamesB):
        pass

    assert Registry.names(_BaseNamesA) == ["alpha", "zebra"]
    assert "unrelated" not in Registry.names(_BaseNamesA)


def test_run_registry_put_and_get_roundtrip():
    class _BaseRunPutGet:
        pass

    class _ImplRunPutGet(_BaseRunPutGet):
        def __init__(self, value):
            self.value = value

    run = RunRegistry()
    instance = _ImplRunPutGet("hello")
    run.put(_BaseRunPutGet, "thing", instance)

    assert run.get(_BaseRunPutGet, "thing") is instance
    assert run[_BaseRunPutGet, "thing"] is instance


def test_run_registry_unregistered_name_raises_key_error_with_name_in_message():
    class _BaseRunUnregistered:
        pass

    run = RunRegistry()
    with pytest.raises(KeyError, match="missing"):
        run.get(_BaseRunUnregistered, "missing")


def test_run_registry_instances_are_isolated_per_registry():
    class _BaseRunIsolated:
        pass

    run_a = RunRegistry()
    run_b = RunRegistry()
    run_a.put(_BaseRunIsolated, "thing", _BaseRunIsolated())

    with pytest.raises(KeyError):
        run_b.get(_BaseRunIsolated, "thing")
