from .live import run
from .used_across_modules import used_across_modules
from namespace_pkg.live import namespace_value

run(namespace_value)
print(used_across_modules())
