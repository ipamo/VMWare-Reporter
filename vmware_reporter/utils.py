from pathlib import Path
from reporter_utils import get_prog_name

__prog_modulepath__ = Path(__file__).resolve().parent
__prog__ = get_prog_name(__prog_modulepath__)
