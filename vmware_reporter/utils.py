from __future__ import annotations

import atexit
import json
import logging
import logging.config
import os
import re
import sys
from textwrap import dedent
import unicodedata
from argparse import ArgumentParser, RawTextHelpFormatter, _SubParsersAction
from configparser import ConfigParser
from contextlib import nullcontext
from datetime import date, datetime, time, timedelta
from decimal import Decimal
from enum import Enum, Flag
from io import IOBase
from locale import LC_ALL, localeconv, setlocale, format_string
from pathlib import Path
from traceback import format_exception
from types import FunctionType, TracebackType
from uuid import UUID

logger = logging.getLogger(__name__)

__prog__ = 'vmware-reporter'


#region Text

def slugify(value: str, separator: str = '-', keep: str = None, strip_separator: bool = True, strip_keep: bool = True, if_none: str = None) -> str:
    """ 
    Generate a slug.
    """
    if value is None:
        return if_none
    
    separator = separator if separator is not None else ''
    keep = keep if keep is not None else ''

    # Normalize the string: replace diacritics by standard characters, lower the string, etc
    value = str(value)
    value = unicodedata.normalize("NFKD", value).encode("ascii", "ignore").decode("ascii")
    value = value.lower()

    # Remove special characters
    remove_sequence = r'^a-zA-Z0-9\s' + re.escape(separator) + re.escape(keep)
    value = re.sub(f"[{remove_sequence}]", "", value)

    # Replace spaces and successive separators by a single separator
    replace_sequence = r'\s' + re.escape(separator)
    value = re.sub(f"[{replace_sequence}]+", separator, value)
    
    # Strips separator and kept characters
    strip_chars = (separator if strip_separator else '') + (keep if strip_keep else '')
    value = value.strip(strip_chars)

    return value


class Filter:
    def __init__(self, spec: str|re.Pattern, *, normalize: bool = False):
        self.normalize = normalize

        if isinstance(spec, re.Pattern):
            self.regex = spec

        elif isinstance(spec, str) and spec.startswith('^'):
            m = re.match(r'^(.*\$)(A|I|L|U|M|S|X)+$', spec, re.IGNORECASE)
            if m:
                pattern = m[1]
                flags = re.NOFLAG
                for letter in m[2]:
                    flags |= re.RegexFlag[letter.upper()]
            else:
                pattern = spec
                flags = re.NOFLAG

            self.regex = re.compile(pattern, flags)

        elif isinstance(spec, str):
            if self.normalize:
                spec = self.normalize_spec(spec)

            if '*' in spec:
                name_parts = spec.split('*')
                pattern_parts = [re.escape(name_part) for name_part in name_parts]
                pattern = r'^' + r'.*'.join(pattern_parts) + r'$'
                self.regex = re.compile(pattern)
            else:
                self.regex = spec

        else:
            raise TypeError(f"filter spec must be a string or regex pattern, got {type(spec).__name__}")
       

    def __repr__(self) -> str:
        return self.regex.pattern if isinstance(self.regex, re.Pattern) else self.regex


    def matches(self, value: str, is_normalized: bool = False):
        if value is None:
            value = ""
        elif not isinstance(value, str):
            value = str(value)

        if self.normalize and not is_normalized:
            value = self.normalize_value(value)

        if isinstance(self.regex, re.Pattern):
            if self.regex.match(value):
                return True
            
        elif self.regex == value:
            return True


    @classmethod
    def normalize_spec(cls, spec: str):
        return slugify(spec, separator=None, keep='*', strip_keep=False, if_none=None)
    
    
    @classmethod
    def normalize_value(cls, value: str):
        return slugify(value, separator=None, keep=None, if_none=None)


class Filters:
    def __init__(self, specs: list[str|re.Pattern]|str|re.Pattern, *, normalize: bool = False):
        self.filters: list[Filter] = []

        if specs:
            if isinstance(specs, (str,re.Pattern)):
                specs = [specs]

            for spec in specs:
                self.filters.append(Filter(spec, normalize=normalize))


    def __len__(self):
        return len(self.filters)


    def matches(self, value: str, if_no_filter: bool = False):
        if not self.filters:
            return if_no_filter
        
        if value is None:
            value = ""
        elif not isinstance(value, str):
            value = str(value)
        
        normalized_value = None    

        for str_filter in self.filters:
            if str_filter.normalize:
                if normalized_value is None:
                    normalized_value = Filter.normalize_value(value)
                if str_filter.matches(normalized_value, is_normalized=True):
                    return True
            else:
                if str_filter.matches(value):
                    return True
                
        return False

#endregion


#region Numbers

class Bytes(int):
    def __add__(self, __value: int) -> Bytes:
        return Bytes(super().__add__(__value))
    
    def __sub__(self, __value: int) -> Bytes:
        return Bytes(super().__sub__(__value))

    def __mul__(self, __value: int) -> Bytes:
        return Bytes(super().__mul__(__value))
    

def human_bytes(value: int, *, unit: str = 'iB', divider: int = 1024, decimals: int = 1, decimal_separator: str = None, thousands_separator: str = None, max_multiple: str = None) -> str:
    """
    Get a human-readable representation of a number of bytes.

    `max_multiple` may be `K`, `M`, `G'` or `T'. 
    """
    return human_number(value, unit=unit, divider=divider, decimals=decimals, decimal_separator=decimal_separator, thousands_separator=thousands_separator, max_multiple=max_multiple)


def human_number(value: int, *, unit: str = '', divider: int = 1000, decimals: int = 1, decimal_separator: str = None, thousands_separator: str = None, max_multiple: str = None) -> str:
    """
    Get a human-readable representation of a number.

    `max_multiple` may be `K`, `M`, `G'` or `T'. 
    """
    if value is None:
        return None

    suffixes = []

    # Append non-multiple suffix (bytes)
    # (if unit is 'iB' we dont display the 'i' as it makes more sens to display "123 B" than "123 iB")
    if unit:
        suffixes.append(' ' + (unit[1:] if len(unit) >= 2 and unit[0] == 'i' else unit))
    else:
        suffixes.append('')

    # Append multiple suffixes
    for multiple in ['K', 'M', 'G', 'T']:
        suffixes.append(f' {multiple}{unit}')
        if max_multiple and max_multiple.upper() == multiple:
            break

    i = 0
    suffix = suffixes[i]
    divided_value = value

    while divided_value > 1000 and i < len(suffixes) - 1:
        divided_value /= divider
        i += 1
        suffix = suffixes[i]

    # Format value
    formatted_value = format_string('%d' if i == 0 else f'%.{decimals}f', divided_value, grouping=True)
    
    # Display formatted value with suffix
    return f'{formatted_value}{suffix}'

#endregion
    

#region JSON
    
class ExtendedJSONEncoder(json.JSONEncoder):
    """
    Adapted from: django.core.serializers.json.DjangoJSONEncoder
    
    Usage example: json.dumps(data, indent=4, cls=ExtendedJSONEncoder)
    """
    def __init__(self, **kwargs):
        super().__init__(**kwargs)

    def default(self, o):
        if isinstance(o, datetime):
            r = o.isoformat()
            if o.microsecond and o.microsecond % 1000 == 0:
                r = r[:23] + r[26:]
            if r.endswith("+00:00"):
                r = r[:-6] + "Z"
            return r
        elif isinstance(o, date):
            return o.isoformat()
        elif isinstance(o, time):
            if o.tzinfo is not None:
                raise ValueError("JSON can't represent timezone-aware times.")
            r = o.isoformat()
            if o.microsecond and o.microsecond % 1000 == 0:
                r = r[:12]
            return f'T{r}'
        elif isinstance(o, timedelta):
            return duration_iso_string(o)
        elif isinstance(o, (Decimal, UUID)):
            return str(o)
        elif isinstance(o, (Enum,Flag)):
            return o.name
        else:
            return super().default(o)

#endregion


#region Time

def duration_iso_string(duration: timedelta):
    # Adapted from: django.utils.duration.duration_iso_string
    if duration < timedelta(0):
        sign = "-"
        duration *= -1
    else:
        sign = ""

    days, hours, minutes, seconds, microseconds = _get_duration_components(duration)
    ms = ".{:06d}".format(microseconds) if microseconds else ""
    return "{}P{}DT{:02d}H{:02d}M{:02d}{}S".format(
        sign, days, hours, minutes, seconds, ms
    )


def _get_duration_components(duration: timedelta):
    days = duration.days
    seconds = duration.seconds
    microseconds = duration.microseconds

    minutes = seconds // 60
    seconds = seconds % 60

    hours = minutes // 60
    minutes = minutes % 60

    return days, hours, minutes, seconds, microseconds

#endregion


#region Tables

def tabulate(data: list[list|dict], headers: list[str] = None, *, out: os.PathLike|IOBase = None, title: str = None, csv: bool = None):
    fmt = TabulateFormat(csv)
    
    if not out or out == 'stdout':
        out = sys.stdout
    elif out == 'stderr':
        out = sys.stderr

    if isinstance(out, IOBase):
        if fmt.csv is None:
            fmt.csv = False
        out_name = getattr(out, 'name', '<io>')
    else:
        if fmt.csv is None:
            fmt.csv = True                    
        if parent := os.path.dirname(out):
            os.makedirs(parent, exist_ok=True)
        out_name = str(out)

    data, headers = fmt.prepare(data, headers)
        
    if title:
        logger.info(f"Export {title} to {out_name}")

    with nullcontext(out) if isinstance(out, IOBase) else open(out, 'w', encoding='utf-8-sig') as fp:
        fmt.export(fp, data, headers)


class TabulateFormat:
    locale = None

    def __init__(self, csv: bool = None):
        if not self.__class__.locale:
            self.__class__.locale = setlocale(LC_ALL, '')

        self.csv = csv
        self.csv_separator = ';' if localeconv()["decimal_point"] == ',' else '.'
        self.csv_quotechar = '"'
        
        self.today = datetime.today().date()


    def _escape(self, value):
        if not isinstance(value, str):
            value = str(value)

        csv_need_escape = False
        result = ''
        for c in value:
            if c == '\r':
                continue # ignore
            elif c == '\n':
                if self.csv:
                    result += c
                    csv_need_escape = True
                else:
                    result += ' '
            elif c == '\t':
                if self.csv:
                    result += c
                else:
                    result += ' '
            elif c == self.csv_separator:
                result += c
                if self.csv:
                    csv_need_escape = True
            elif c == self.csv_quotechar:
                if self.csv:
                    result += f'{c}{c}'
                    csv_need_escape = True
                else:
                    result += c
            else:
                result += c

        if csv_need_escape:
            return f'{self.csv_quotechar}{result}{self.csv_quotechar}'
        else:
            return result
        
    
    def _convert(self, value, header: TabulateHeader|None):
        if value is None:
            return '\"\"' if self.csv else ''
        
        if isinstance(value, Bytes):
            if self.csv:
                return value
            else:
                return human_bytes(value)
        
        if isinstance(value, datetime):
            if value.tzinfo:
                value = value.astimezone()
                if self.csv:
                    return self._escape(value.replace(tzinfo=None).strftime('%Y-%m-%d %H:%M:%S')) # for display in Excel (does not support timezones)
                else:
                    return self._escape(value.strftime('%H:%M:%S') if value.date() == self.today else value.strftime('%Y-%m-%d'))
            else:
                return self._escape(value.replace(microsecond=0))
        
        return self._escape(value)


    def prepare(self, data: list[list|dict], headers: list[str] = None) -> tuple[list[list[str]],list[TabulateHeader]]:
        """
        Prepare data and headers:
        - headers are computed from data if all rows are dictionaries ;
        - dictionary rows are transformed to list.
        """
        # Create TabulateHeader objects
        if not headers:
            dict_headers: dict[str,TabulateHeader] = {}
            not_only_dicts = False
            for row in data:
                if isinstance(row, dict):
                    for key in row:
                        if not key in dict_headers:
                            dict_headers[key] = TabulateHeader(self._convert(key, None), key=key)
                else:
                    not_only_dicts = True

            if dict_headers and not_only_dicts:
                raise ValueError("Cannot determine headers from data: some rows are dicts, other are not.")
            
            prepared_headers = list(dict_headers.values()) if dict_headers else []

        else:
            prepared_headers = [TabulateHeader(self._convert(key, None), key=key) for key in headers]


        # Convert rows
        prepared_data = []
        for row in data:
            if isinstance(row, dict):
                # NOTE: here, headers necessarily exists
                prepared_row = []
                for header in prepared_headers:
                    value = row.get(header.key)
                    if not self.csv and not value is None:
                        header.value_types.add(type(value))
                    prepared_row.append(self._convert(value, header))
            
            else:
                prepared_row = []
                for i, value in enumerate(row):
                    if i >= len(prepared_headers):
                        header = TabulateHeader('')
                        prepared_headers.append(header)
                    else:
                        header = prepared_headers[i]

                    if not self.csv and not value is None:
                        header.value_types.add(type(value))
                    prepared_row.append(self._convert(value, header))

            prepared_data.append(prepared_row)

        # Determine alignment
        if not self.csv:
            for header in prepared_headers:
                if all(issubclass(t, int) for t in header.value_types):
                    header.justify_func = str.rjust

        return prepared_data, prepared_headers


    def export(self, fp: IOBase, data: list[list[str]], headers: list[TabulateHeader] = None):
        if self.csv:
            self._export_csv(fp, data, headers)
        else:
            self._export_noncsv(fp, data, headers)
    

    def _export_csv(self, fp: IOBase, data: list[list[str]], headers: list[TabulateHeader] = None):
        if headers:
            for i, header in enumerate(headers):
                if i > 0:
                    fp.write(self.csv_separator)
                fp.write(header.name)

        for n, row in enumerate(data):
            fp.write('\n')
            if n % 100 == 0:
                fp.flush()
            
            for i, value in enumerate(row):
                if i > 0:
                    fp.write(self.csv_separator)
                fp.write(value)
                
        fp.flush()


    def _export_noncsv(self, fp: IOBase, data: list[list[str]], headers: list[TabulateHeader] = None):
        # determine column parameters
        for header in headers:
            header.max_width = len(header.name)
        
        for row in data:
            for i, value in enumerate(row):
                header = headers[i]

                value_len = len(value)
                if value_len > header.max_width:
                    header.max_width = value_len

        # display headers and separator row
        if headers:
            separator_row = ''
            for i, header in enumerate(headers):
                if i > 0:
                    fp.write(' | ')
                    separator_row += ' | '
                fp.write(header.justify_func(header.name, header.max_width))
                separator_row += header.justify_func('-', header.max_width, '-')
            fp.write('\n')

            fp.write(separator_row)
            fp.write('\n')

        # display data rows
        for n, row in enumerate(data):
            for i, value in enumerate(row):
                if i > 0:
                    fp.write(' | ')
                header = headers[i]
                fp.write(header.justify_func(value, headers[i].max_width))
            
            fp.write('\n')
            if n % 100 == 0:
                fp.flush()
                
        fp.flush()

class TabulateHeader:
    def __init__(self, name: str, *, key = None):
        self.name = name
        self.key = key
        self.max_width: int = None
        self.value_types: set[type] = set()
        self.justify_func = str.ljust

#endregion


#region Commands
    
def add_func_command(subparsers: _SubParsersAction[ArgumentParser], handle: FunctionType, add_arguments: FunctionType = None, *, name: str = None, doc: str = None, **defaults):
    """
    Add the given function as a subcommand of the parser.
    """
    if name is None:
        name = handle.__name__
    if doc is None:
        doc = handle.__doc__

    cmdparser = subparsers.add_parser(name, help=get_help_text(doc), description=dedent(doc) if doc else None, formatter_class=RawTextHelpFormatter)
    cmdparser.set_defaults(handle=handle, **defaults)

    if not add_arguments:
        add_arguments = getattr(handle, 'add_arguments', None)

    if add_arguments:
        add_arguments(cmdparser)

    return cmdparser


def get_help_text(docstring: str):
    if docstring is None:
        return None
    
    docstring = docstring.strip()
    try:
        return docstring[0:docstring.index('\n')].strip()
    except:
        return docstring


#endregion


#region Config
    
EMBEDDED_APPDATA = Path(__file__).resolve().parent.joinpath('appdata')
SYSTEM_APPDATA = Path(os.environ.get('ProgramData', 'C:\\ProgramData' if sys.platform == 'win32' else '/etc')).joinpath(__prog__)
USER_APPDATA = Path(os.environ.get('LOCALAPPDATA', '~\\AppData\Local' if sys.platform == 'win32' else '~/.config')).joinpath(__prog__)
LOCAL_APPDATA = Path.cwd().joinpath('data')

_config: ConfigParser = None

def get_appdata_paths(name: str = ''):
    possible_paths = [
        # Embedded configuration
        EMBEDDED_APPDATA.joinpath(name),
        # System configuration
        SYSTEM_APPDATA.joinpath(name),
        # User configuration
        USER_APPDATA.joinpath(name),
        # Local configuration
        LOCAL_APPDATA.joinpath(name),
    ]

    paths = []
    for path in possible_paths:
        if path.exists():
            paths.append(path)

    return paths

def get_config():
    global _config

    if _config is None:
        _config = ConfigParser()

        paths = get_appdata_paths(f'{__prog__}.conf')
        if paths:
            for path in paths:
                logger.debug("Config file: %s", path)
            _config.read(paths)

        else:
            logger.warning("No config file found")

    return _config

#endregion


#region Logging

def configure_logging(level: str|int = False):
    try:
        import colorlog
    except ImportError:
        colorlog = None

    if isinstance(level, int):
        level = logging.getLevelName(level)
    
    config = {
        'version': 1,
        'disable_existing_loggers': False,
        'formatters': {
            'default': {
                'format': '%(levelname)s [%(name)s] %(message)s',
            },
        },
        'handlers': {
            'console': {
                'class': 'logging.StreamHandler',
                'formatter': 'default',
            },
            'exit': {
                'class': ExitHandler.__module__ + '.' + ExitHandler.__qualname__,
                'level': 'WARNING',
            },
        },
        'root': {
            'handlers': ['console', 'exit'],
            'level': level if level else (os.environ.get('LOG_LEVEL', '').upper() or 'INFO'),
        },
    }
    
    if colorlog:
        config['formatters']['colored'] = {
            '()': 'colorlog.ColoredFormatter',
            'format': '%(log_color)s%(levelname)s%(reset)s %(light_black)s[%(name)s]%(reset)s %(log_color)s%(message)s%(reset)s',
        }
        config['handlers']['console']['formatter'] = 'colored'

    logging.config.dictConfig(config)


class ExitHandler(logging.Handler):
    """
    A logging handler that counts warnings and errors.
    
    If warnings and errors occured during the program execution, display counts at exit
    and set exit code to 68 (EADV) (if it was not explicitely set with `sys.exit` function).
    """
    _counts: dict[int, int] = {}
    _detected_exception: tuple[type[BaseException], BaseException, TracebackType|None] = None
    _detected_exit_code = 0

    _original_exit = sys.exit
    _original_excepthook = sys.excepthook

    _already_registered = False

    def __init__(self):
        if not self.__class__._already_registered:
            sys.exit = self.__class__._exit
            sys.excepthook = self.__class__._excepthook
            atexit.register(self.__class__._atexit)
            self.__class__._already_registered = True
        
        super().__init__(level=logging.WARNING)

    def emit(self, record: logging.LogRecord):
        if record.levelno >= self.level:
            if not record.levelno in self.__class__._counts:
                self.__class__._counts[record.levelno] = 1
            else:
                self.__class__._counts[record.levelno] += 1
    
    @classmethod
    def _exit(cls, code: int = 0):
        cls._detected_exit_code = code
        cls._original_exit(code)
    
    @classmethod
    def _excepthook(cls, exc_type: type[BaseException], exc_value: BaseException, exc_traceback: TracebackType|None):
        cls._detected_exception = exc_type, exc_value, exc_traceback
        cls._original_exit(1)

    @classmethod
    def _atexit(cls):
        if cls._detected_exception:
            exc_type, exc_value, exc_traceback = cls._detected_exception

            msg = 'An unhandled exception occured\n'
            msg += ''.join(format_exception(exc_type, exc_value, exc_traceback)).strip()
            logger.critical(msg)

        else:
            level = None
            msg = ''
            
            for levelno in sorted(cls._counts.keys(), reverse=True):
                if level is None:
                    level = levelno
                levelname = logging.getLevelName(levelno)
                msg += (', ' if msg else 'Logged ') + f'{levelname}: {cls._counts[levelno]}'

            if level is not None:
                logger.log(level, msg)
                
                # Change exit code if it was not originally set explicitely to another value, using `sys.exit()`
                if cls._detected_exit_code == 0:
                    os._exit(68) # EADV (Advertise error) = 68

#endregion
