from tam.marketdata import reference_schema as schema


def test_pyarrow_schema_builds_for_every_dataset():
    for columns in (
        schema.SPLIT_COLUMNS,
        schema.DIVIDEND_COLUMNS,
        schema.IPO_COLUMNS,
        schema.SHORT_VOLUME_COLUMNS,
        schema.SHORT_INTEREST_COLUMNS,
        schema.FLOAT_COLUMNS,
    ):
        built = schema.pyarrow_schema(columns)
        assert built.names == columns


def test_every_column_has_a_type_mapping():
    all_columns = (
        schema.SPLIT_COLUMNS
        + schema.DIVIDEND_COLUMNS
        + schema.IPO_COLUMNS
        + schema.SHORT_VOLUME_COLUMNS
        + schema.SHORT_INTEREST_COLUMNS
        + schema.FLOAT_COLUMNS
    )
    for column in all_columns:
        assert column in schema._COLUMN_TYPE_NAMES, f"{column} has no type mapping"


def test_empty_frame_has_the_right_columns_and_no_rows():
    frame = schema.empty_frame(schema.SPLIT_COLUMNS)
    assert list(frame.columns) == schema.SPLIT_COLUMNS
    assert frame.empty
