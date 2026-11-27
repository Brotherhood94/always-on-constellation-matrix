"""Load the same host implementation used by the isolated Root Operations broker."""

from importlib.util import module_from_spec, spec_from_file_location
from pathlib import Path

path = Path(__file__).resolve().parent.parent / 'integrations/root/maintenance.py'
spec = spec_from_file_location('constellation_host_maintenance', path)
module = module_from_spec(spec)
spec.loader.exec_module(module)
Maintenance = module.Maintenance
ordinary = module.ordinary
