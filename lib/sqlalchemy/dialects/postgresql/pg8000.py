# postgresql/pg8000.py
# Copyright (C) 2005-2021 the SQLAlchemy authors and contributors <see AUTHORS
# file>
#
# This module is part of SQLAlchemy and is released under
# the MIT License: http://www.opensource.org/licenses/mit-license.php
r"""
.. dialect:: postgresql+pg8000
    :name: pg8000
    :dbapi: pg8000
    :connectstring: postgresql+pg8000://user:password@host:port/dbname[?key=value&key=value...]
    :url: https://pypi.org/project/pg8000/

.. versionchanged:: 1.4  The pg8000 dialect has been updated for version
   1.16.6 and higher, and is again part of SQLAlchemy's continuous integration
   with full feature support.

.. _pg8000_unicode:

Unicode
-------

pg8000 will encode / decode string values between it and the server using the
PostgreSQL ``client_encoding`` parameter; by default this is the value in
the ``postgresql.conf`` file, which often defaults to ``SQL_ASCII``.
Typically, this can be changed to ``utf-8``, as a more useful default::

    #client_encoding = sql_ascii # actually, defaults to database
                                 # encoding
    client_encoding = utf8

The ``client_encoding`` can be overridden for a session by executing the SQL:

SET CLIENT_ENCODING TO 'utf8';

SQLAlchemy will execute this SQL on all new connections based on the value
passed to :func:`_sa.create_engine` using the ``client_encoding`` parameter::

    engine = create_engine(
        "postgresql+pg8000://user:pass@host/dbname", client_encoding='utf8')


.. _pg8000_isolation_level:

pg8000 Transaction Isolation Level
-------------------------------------

The pg8000 dialect offers the same isolation level settings as that
of the :ref:`psycopg2 <psycopg2_isolation_level>` dialect:

* ``READ COMMITTED``
* ``READ UNCOMMITTED``
* ``REPEATABLE READ``
* ``SERIALIZABLE``
* ``AUTOCOMMIT``

.. seealso::

    :ref:`postgresql_isolation_level`

    :ref:`psycopg2_isolation_level`


"""  # noqa
import decimal
import re
from uuid import UUID as _python_UUID

from .base import _DECIMAL_TYPES
from .base import _FLOAT_TYPES
from .base import _INT_TYPES
from .base import ENUM
from .base import INTERVAL
from .base import PGCompiler
from .base import PGDialect
from .base import PGExecutionContext
from .base import PGIdentifierPreparer
from .base import UUID
from .json import JSON
from .json import JSONB
from .json import JSONPathType
from ... import exc
from ... import processors
from ... import types as sqltypes
from ... import util
from ...sql.elements import quoted_name


class _PGNumeric(sqltypes.Numeric):
    def result_processor(self, dialect, coltype):
        if self.asdecimal:
            if coltype in _FLOAT_TYPES:
                return processors.to_decimal_processor_factory(
                    decimal.Decimal, self._effective_decimal_return_scale
                )
            elif coltype in _DECIMAL_TYPES or coltype in _INT_TYPES:
                # pg8000 returns Decimal natively for 1700
                return None
            else:
                raise exc.InvalidRequestError(
                    "Unknown PG numeric type: %d" % coltype
                )
        else:
            if coltype in _FLOAT_TYPES:
                # pg8000 returns float natively for 701
                return None
            elif coltype in _DECIMAL_TYPES or coltype in _INT_TYPES:
                return processors.to_float
            else:
                raise exc.InvalidRequestError(
                    "Unknown PG numeric type: %d" % coltype
                )


class _PGNumericNoBind(_PGNumeric):
    def bind_processor(self, dialect):
        return None


class _PGJSON(JSON):
    def result_processor(self, dialect, coltype):
        return None

    def get_dbapi_type(self, dbapi):
        return dbapi.JSON


class _PGJSONB(JSONB):
    def result_processor(self, dialect, coltype):
        return None

    def get_dbapi_type(self, dbapi):
        return dbapi.JSONB


class _PGJSONIndexType(sqltypes.JSON.JSONIndexType):
    def get_dbapi_type(self, dbapi):
        raise NotImplementedError("should not be here")


class _PGJSONIntIndexType(sqltypes.JSON.JSONIntIndexType):
    def get_dbapi_type(self, dbapi):
        return dbapi.INTEGER


class _PGJSONStrIndexType(sqltypes.JSON.JSONStrIndexType):
    def get_dbapi_type(self, dbapi):
        return dbapi.STRING


class _PGJSONPathType(JSONPathType):
    def get_dbapi_type(self, dbapi):
        return 1009


class _PGUUID(UUID):
    def bind_processor(self, dialect):
        if not self.as_uuid:

            def process(value):
                if value is not None:
                    value = _python_UUID(value)
                return value

            return process

    def result_processor(self, dialect, coltype):
        if not self.as_uuid:

            def process(value):
                if value is not None:
                    value = str(value)
                return value

            return process


class _PGEnum(ENUM):
    def get_dbapi_type(self, dbapi):
        return dbapi.UNKNOWN


class _PGInterval(INTERVAL):
    def get_dbapi_type(self, dbapi):
        return dbapi.INTERVAL

    @classmethod
    def adapt_emulated_to_native(cls, interval, **kw):
        return _PGInterval(precision=interval.second_precision)


class _PGTimeStamp(sqltypes.DateTime):
    def get_dbapi_type(self, dbapi):
        if self.timezone:
            # TIMESTAMPTZOID
            return 1184
        else:
            # TIMESTAMPOID
            return 1114


class _PGTime(sqltypes.Time):
    def get_dbapi_type(self, dbapi):
        return dbapi.TIME


class _PGInteger(sqltypes.Integer):
    def get_dbapi_type(self, dbapi):
        return dbapi.INTEGER


class _PGSmallInteger(sqltypes.SmallInteger):
    def get_dbapi_type(self, dbapi):
        return dbapi.INTEGER


class _PGNullType(sqltypes.NullType):
    def get_dbapi_type(self, dbapi):
        return dbapi.NULLTYPE


class _PGBigInteger(sqltypes.BigInteger):
    def get_dbapi_type(self, dbapi):
        return dbapi.BIGINTEGER


class _PGBoolean(sqltypes.Boolean):
    def get_dbapi_type(self, dbapi):
        return dbapi.BOOLEAN


class PGExecutionContext_pg8000(PGExecutionContext):
    def pre_exec(self):
        if not self.compiled:
            return


class PGCompiler_pg8000(PGCompiler):
    def visit_mod_binary(self, binary, operator, **kw):
        return (
            self.process(binary.left, **kw)
            + " %% "
            + self.process(binary.right, **kw)
        )


class PGIdentifierPreparer_pg8000(PGIdentifierPreparer):
    def __init__(self, *args, **kwargs):
        PGIdentifierPreparer.__init__(self, *args, **kwargs)
        self._double_percents = False


class PGDialect_pg8000(PGDialect):
    driver = "pg8000"

    supports_unicode_statements = True

    supports_unicode_binds = True

    default_paramstyle = "format"
    supports_sane_multi_rowcount = True
    execution_ctx_cls = PGExecutionContext_pg8000
    statement_compiler = PGCompiler_pg8000
    preparer = PGIdentifierPreparer_pg8000

    use_setinputsizes = True

    # reversed as of pg8000 1.16.6.  1.16.5 and lower
    # are no longer compatible
    description_encoding = None
    # description_encoding = "use_encoding"

    colspecs = util.update_copy(
        PGDialect.colspecs,
        {
            sqltypes.Numeric: _PGNumericNoBind,
            sqltypes.Float: _PGNumeric,
            sqltypes.JSON: _PGJSON,
            sqltypes.Boolean: _PGBoolean,
            sqltypes.NullType: _PGNullType,
            JSONB: _PGJSONB,
            sqltypes.JSON.JSONPathType: _PGJSONPathType,
            sqltypes.JSON.JSONIndexType: _PGJSONIndexType,
            sqltypes.JSON.JSONIntIndexType: _PGJSONIntIndexType,
            sqltypes.JSON.JSONStrIndexType: _PGJSONStrIndexType,
            UUID: _PGUUID,
            sqltypes.Interval: _PGInterval,
            INTERVAL: _PGInterval,
            sqltypes.DateTime: _PGTimeStamp,
            sqltypes.Time: _PGTime,
            sqltypes.Integer: _PGInteger,
            sqltypes.SmallInteger: _PGSmallInteger,
            sqltypes.BigInteger: _PGBigInteger,
            sqltypes.Enum: _PGEnum,
        },
    )

    def __init__(self, client_encoding=None, **kwargs):
        PGDialect.__init__(self, **kwargs)
        self.client_encoding = client_encoding

        if self._dbapi_version < (1, 16, 6):
            raise NotImplementedError("pg8000 1.16.6 or greater is required")

    @util.memoized_property
    def _dbapi_version(self):
        if self.dbapi and hasattr(self.dbapi, "__version__"):
            return tuple(
                [
                    int(x)
                    for x in re.findall(
                        r"(\d+)(?:[-\.]?|$)", self.dbapi.__version__
                    )
                ]
            )
        else:
            return (99, 99, 99)

    @classmethod
    def dbapi(cls):
        return __import__("pg8000")

    def create_connect_args(self, url):
        opts = url.translate_connect_args(username="user")
        if "port" in opts:
            opts["port"] = int(opts["port"])
        opts.update(url.query)
        return ([], opts)

    def is_disconnect(self, e, connection, cursor):
        if isinstance(
            e, self.dbapi.InterfaceError
        ) and "network  error" in str(e):
            # new as of pg8000 1.19.0 for broken connections
            return True

        # connection was closed normally
        return "connection is closed" in str(e)

    def set_isolation_level(self, connection, level):
        level = level.replace("_", " ")

        # adjust for ConnectionFairy possibly being present
        if hasattr(connection, "connection"):
            connection = connection.connection

        if level == "AUTOCOMMIT":
            connection.autocommit = True
        elif level in self._isolation_lookup:
            connection.autocommit = False
            cursor = connection.cursor()
            cursor.execute(
                "SET SESSION CHARACTERISTICS AS TRANSACTION "
                "ISOLATION LEVEL %s" % level
            )
            cursor.execute("COMMIT")
            cursor.close()
        else:
            raise exc.ArgumentError(
                "Invalid value '%s' for isolation_level. "
                "Valid isolation levels for %s are %s or AUTOCOMMIT"
                % (level, self.name, ", ".join(self._isolation_lookup))
            )

    def set_readonly(self, connection, value):
        cursor = connection.cursor()
        try:
            cursor.execute(
                "SET SESSION CHARACTERISTICS AS TRANSACTION %s"
                % ("READ ONLY" if value else "READ WRITE")
            )
            cursor.execute("COMMIT")
        finally:
            cursor.close()

    def get_readonly(self, connection):
        cursor = connection.cursor()
        try:
            cursor.execute("show transaction_read_only")
            val = cursor.fetchone()[0]
        finally:
            cursor.close()

        return val == "on"

    def set_deferrable(self, connection, value):
        cursor = connection.cursor()
        try:
            cursor.execute(
                "SET SESSION CHARACTERISTICS AS TRANSACTION %s"
                % ("DEFERRABLE" if value else "NOT DEFERRABLE")
            )
            cursor.execute("COMMIT")
        finally:
            cursor.close()

    def get_deferrable(self, connection):
        cursor = connection.cursor()
        try:
            cursor.execute("show transaction_deferrable")
            val = cursor.fetchone()[0]
        finally:
            cursor.close()

        return val == "on"

    def set_client_encoding(self, connection, client_encoding):
        # adjust for ConnectionFairy possibly being present
        if hasattr(connection, "connection"):
            connection = connection.connection

        cursor = connection.cursor()
        cursor.execute("SET CLIENT_ENCODING TO '" + client_encoding + "'")
        cursor.execute("COMMIT")
        cursor.close()

    def do_set_input_sizes(self, cursor, list_of_tuples, context):
        if self.positional:
            cursor.setinputsizes(
                *[dbtype for key, dbtype, sqltype in list_of_tuples]
            )
        else:
            cursor.setinputsizes(
                **{
                    key: dbtype
                    for key, dbtype, sqltype in list_of_tuples
                    if dbtype
                }
            )

    def do_begin_twophase(self, connection, xid):
        connection.connection.tpc_begin((0, xid, ""))

    def do_prepare_twophase(self, connection, xid):
        connection.connection.tpc_prepare()

    def do_rollback_twophase(
        self, connection, xid, is_prepared=True, recover=False
    ):
        connection.connection.tpc_rollback((0, xid, ""))

    def do_commit_twophase(
        self, connection, xid, is_prepared=True, recover=False
    ):
        connection.connection.tpc_commit((0, xid, ""))

    def do_recover_twophase(self, connection):
        return [row[1] for row in connection.connection.tpc_recover()]

    def on_connect(self):
        fns = []

        def on_connect(conn):
            conn.py_types[quoted_name] = conn.py_types[util.text_type]

        fns.append(on_connect)

        if self.client_encoding is not None:

            def on_connect(conn):
                self.set_client_encoding(conn, self.client_encoding)

            fns.append(on_connect)

        if self.isolation_level is not None:

            def on_connect(conn):
                self.set_isolation_level(conn, self.isolation_level)

            fns.append(on_connect)

        if self._json_deserializer:

            def on_connect(conn):
                # json
                conn.register_in_adapter(114, self._json_deserializer)

                # jsonb
                conn.register_in_adapter(3802, self._json_deserializer)

            fns.append(on_connect)

        if len(fns) > 0:

            def on_connect(conn):
                for fn in fns:
                    fn(conn)

            return on_connect
        else:
            return None


dialect = PGDialect_pg8000
