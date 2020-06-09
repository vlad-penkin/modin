# Licensed to Modin Development Team under one or more contributor license agreements.
# See the NOTICE file distributed with this work for additional information regarding
# copyright ownership.  The Modin Development Team licenses this file to you under the
# Apache License, Version 2.0 (the "License"); you may not use this file except in
# compliance with the License.  You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software distributed under
# the License is distributed on an "AS IS" BASIS, WITHOUT WARRANTIES OR CONDITIONS OF
# ANY KIND, either express or implied. See the License for the specific language
# governing permissions and limitations under the License.

from modin.backends.base.query_compiler import BaseQueryCompiler
from modin.backends.pandas.query_compiler import PandasQueryCompiler
from modin.error_message import ErrorMessage

import pandas
import abc


def DFAlgNotSupported(fn_name):
    def fn(*args, **kwargs):
        raise NotImplementedError(
            "{} is not yet suported in DFAlgQueryCompiler".format(fn_name)
        )

    return fn


class DFAlgQueryCompiler(BaseQueryCompiler):
    """This class implements the logic necessary for operating on partitions
        with a lazy DataFrame Algebra based backend."""

    default_for_empty = False

    def __init__(self, frame):
        assert frame is not None
        self._modin_frame = frame

    def to_pandas(self):
        return self._modin_frame.to_pandas()

    @classmethod
    def from_pandas(cls, df, data_cls):
        return cls(data_cls.from_pandas(df))

    default_to_pandas = PandasQueryCompiler.default_to_pandas

    def copy(self):
        return self.__constructor__(self._modin_frame)

    def getitem_column_array(self, key, numeric=False):
        if numeric:
            new_modin_frame = self._modin_frame.mask(col_numeric_idx=key)
        else:
            new_modin_frame = self._modin_frame.mask(col_indices=key)
        return self.__constructor__(new_modin_frame)

    # Merge

    def join(self, *args, **kwargs):
        on = kwargs.get("on", None)
        left_index = kwargs.get("left_index", False)
        right_index = kwargs.get("right_index", False)
        """Only non-index joins with explicit 'on' are supported"""
        if left_index is False and right_index is False and on is not None:
            right = args[0]
            how = kwargs.get("how", "inner")
            sort = kwargs.get("sort", False)
            suffixes = kwargs.get("suffixes", None)
            return self.__constructor__(
                self._modin_frame.join(
                    right._modin_frame, how=how, on=on, sort=sort, suffixes=suffixes,
                )
            )
        else:
            return self.default_to_pandas(pandas.DataFrame.merge, *args, **kwargs)

    def view(self, index=None, columns=None):
        return self.__constructor__(
            self._modin_frame.mask(row_numeric_idx=index, col_numeric_idx=columns)
        )

    def groupby_sum(self, by, axis, groupby_args, **kwargs):
        """Groupby with sum aggregation.

        Parameters
        ----------
        by
            The column value to group by. This can come in the form of a query compiler
        axis : (0 or 1)
            The axis the group by
        groupby_args : dict of {"str": value}
            The arguments for groupby. These can include 'level', 'sort', 'as_index',
            'group_keys', and 'squeeze'.
        kwargs
            The keyword arguments for the sum operation

        Returns
        -------
        PandasQueryCompiler
            A new PandasQueryCompiler
        """
        new_frame = self._modin_frame.groupby_agg(
            by, axis, "sum", groupby_args, **kwargs
        )
        new_qc = self.__constructor__(new_frame)
        if groupby_args["squeeze"]:
            new_qc = new_qc.squeeze()
        return new_qc

    def groupby_dict_agg(self, by, func_dict, groupby_args, agg_args, drop=False):
        """Apply aggregation functions to a grouped dataframe per-column.

        Parameters
        ----------
        by : PandasQueryCompiler
            The column to group by
        func_dict : dict of str, callable/string
            The dictionary mapping of column to function
        groupby_args : dict
            The dictionary of keyword arguments for the group by.
        agg_args : dict
            The dictionary of keyword arguments for the aggregation functions
        drop : bool
            Whether or not to drop the column from the data.

        Returns
        -------
        PandasQueryCompiler
            The result of the per-column aggregations on the grouped dataframe.
        """
        # TODO: handle drop arg
        new_frame = self._modin_frame.groupby_agg(
            by, 0, func_dict, groupby_args, **agg_args
        )
        new_qc = self.__constructor__(new_frame)
        if groupby_args["squeeze"]:
            new_qc = new_qc.squeeze()
        return new_qc

    def _get_index(self):
        return self._modin_frame.index

    def _set_index(self, index):
        self._modin_frame.index = index

    def _get_columns(self):
        return self._modin_frame.columns

    def _set_columns(self, columns):
        self._modin_frame = self._modin_frame._set_columns(columns)

    def fillna(
        self,
        value=None,
        method=None,
        axis=None,
        inplace=False,
        limit=None,
        downcast=None,
    ):
        assert not inplace, "inplace=True should be handled on upper level"
        new_frame = self._modin_frame.fillna(
            value=value, method=method, axis=axis, limit=limit, downcast=downcast,
        )
        return self.__constructor__(new_frame)

    def concat(self, axis, other, **kwargs):
        """Concatenates two objects together.

        Args:
            axis: The axis index object to join (0 for columns, 1 for index).
            other: The other_index to concat with.

        Returns:
            Concatenated objects.
        """
        if not isinstance(other, list):
            other = [other]
        assert all(
            isinstance(o, type(self)) for o in other
        ), "Different Manager objects are being used. This is not allowed"
        sort = kwargs.get("sort", None)
        if sort is None:
            sort = False
        join = kwargs.get("join", "outer")
        ignore_index = kwargs.get("ignore_index", False)
        other_modin_frames = [o._modin_frame for o in other]

        new_modin_frame = self._modin_frame._concat(
            axis, other_modin_frames, join=join, sort=sort, ignore_index=ignore_index
        )
        return self.__constructor__(new_modin_frame)

    def drop(self, index=None, columns=None):
        """Remove row data for target index and columns.

        Args:
            index: Target index to drop.
            columns: Target columns to drop.

        Returns:
            A new QueryCompiler.
        """
        assert index == None, "Only column drop is supported"
        return self.__constructor__(
            self._modin_frame.mask(
                row_indices=index, col_indices=self.columns.drop(columns)
            )
        )

    def _bin_op(self, other, op_name, **kwargs):
        level = kwargs.get("level", None)
        if level is not None:
            raise NotImplementedError(f"{op_name} doesn't support levels")

        if isinstance(other, DFAlgQueryCompiler):
            other = other._modin_frame

        new_modin_frame = self._modin_frame.bin_op(other, op_name, **kwargs)
        return self.__constructor__(new_modin_frame)

    def add(self, other, **kwargs):
        return self._bin_op(other, "add", **kwargs)

    def sub(self, other, **kwargs):
        return self._bin_op(other, "sub", **kwargs)

    def reset_index(self, **kwargs):
        level = kwargs.get("level", None)
        if level is not None:
            raise NotImplementedError("reset_index doesn't support level arg yet")

        drop = kwargs.get("drop", False)

        return self.__constructor__(self._modin_frame.reset_index(drop))

    def has_multiindex(self):
        return self._modin_frame.has_multiindex()

    def free(self):
        return

    index = property(_get_index, _set_index)
    columns = property(_get_columns, _set_columns)

    @property
    def dtypes(self):
        return self._modin_frame.dtypes

    __and__ = DFAlgNotSupported("__and__")
    __or__ = DFAlgNotSupported("__or__")
    __rand__ = DFAlgNotSupported("__rand__")
    __ror__ = DFAlgNotSupported("__ror__")
    __rxor__ = DFAlgNotSupported("__rxor__")
    __xor__ = DFAlgNotSupported("__xor__")
    abs = DFAlgNotSupported("abs")
    add_prefix = DFAlgNotSupported("add_prefix")
    add_suffix = DFAlgNotSupported("add_suffix")
    all = DFAlgNotSupported("all")
    any = DFAlgNotSupported("any")
    apply = DFAlgNotSupported("apply")
    applymap = DFAlgNotSupported("applymap")
    astype = DFAlgNotSupported("astype")
    back = DFAlgNotSupported("back")
    clip = DFAlgNotSupported("clip")
    combine = DFAlgNotSupported("combine")
    combine_first = DFAlgNotSupported("combine_first")
    count = DFAlgNotSupported("count")
    cummax = DFAlgNotSupported("cummax")
    cummin = DFAlgNotSupported("cummin")
    cumprod = DFAlgNotSupported("cumprod")
    cumsum = DFAlgNotSupported("cumsum")
    describe = DFAlgNotSupported("describe")
    diff = DFAlgNotSupported("diff")
    dropna = DFAlgNotSupported("dropna")
    eq = DFAlgNotSupported("eq")
    eval = DFAlgNotSupported("eval")
    first_valid_index = DFAlgNotSupported("first_valid_index")
    floordiv = DFAlgNotSupported("floordiv")
    front = DFAlgNotSupported("front")
    ge = DFAlgNotSupported("ge")
    get_dummies = DFAlgNotSupported("get_dummies")
    getitem_row_array = DFAlgNotSupported("getitem_row_array")
    groupby_agg = DFAlgNotSupported("groupby_agg")
    groupby_reduce = DFAlgNotSupported("groupby_reduce")
    gt = DFAlgNotSupported("gt")
    head = DFAlgNotSupported("head")
    idxmax = DFAlgNotSupported("idxmax")
    idxmin = DFAlgNotSupported("idxmin")
    insert = DFAlgNotSupported("insert")
    isin = DFAlgNotSupported("isin")
    isna = DFAlgNotSupported("isna")
    last_valid_index = DFAlgNotSupported("last_valid_index")
    le = DFAlgNotSupported("le")
    lt = DFAlgNotSupported("lt")
    max = DFAlgNotSupported("max")
    mean = DFAlgNotSupported("mean")
    median = DFAlgNotSupported("median")
    memory_usage = DFAlgNotSupported("memory_usage")
    min = DFAlgNotSupported("min")
    mod = DFAlgNotSupported("mod")
    mode = DFAlgNotSupported("mode")
    mul = DFAlgNotSupported("mul")
    ne = DFAlgNotSupported("ne")
    negative = DFAlgNotSupported("negative")
    notna = DFAlgNotSupported("notna")
    nunique = DFAlgNotSupported("nunique")
    pow = DFAlgNotSupported("pow")
    prod = DFAlgNotSupported("prod")
    quantile_for_list_of_values = DFAlgNotSupported("quantile_for_list_of_values")
    quantile_for_single_value = DFAlgNotSupported("quantile_for_single_value")
    query = DFAlgNotSupported("query")
    rank = DFAlgNotSupported("rank")
    reindex = DFAlgNotSupported("reindex")
    rfloordiv = DFAlgNotSupported("rfloordiv")
    rmod = DFAlgNotSupported("rmod")
    round = DFAlgNotSupported("round")
    rpow = DFAlgNotSupported("rpow")
    rsub = DFAlgNotSupported("rsub")
    rtruediv = DFAlgNotSupported("rtruediv")
    skew = DFAlgNotSupported("skew")
    sort_index = DFAlgNotSupported("sort_index")
    std = DFAlgNotSupported("std")
    sum = DFAlgNotSupported("sum")
    tail = DFAlgNotSupported("tail")
    to_datetime = DFAlgNotSupported("to_datetime")
    to_numpy = DFAlgNotSupported("to_numpy")
    transpose = DFAlgNotSupported("transpose")
    truediv = DFAlgNotSupported("truediv")
    unique = DFAlgNotSupported("unique")
    update = DFAlgNotSupported("update")
    var = DFAlgNotSupported("var")
    where = DFAlgNotSupported("where")
    write_items = DFAlgNotSupported("write_items")