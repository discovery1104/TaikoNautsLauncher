from __future__ import annotations

from Cython.Build import cythonize
from setuptools import Extension, setup


extension = Extension(
    name="launcher_core",
    sources=["launcher_core.pyx"],
    define_macros=[("NDEBUG", "1")],
    extra_compile_args=["/O2", "/GL", "/Gy", "/Gw"],
    extra_link_args=["/LTCG", "/OPT:REF", "/OPT:ICF"],
)

setup(
    name="TaikoNautsLauncherCore",
    version="1.0.0",
    ext_modules=cythonize(
        [extension],
        compiler_directives={
            "language_level": 3,
            "annotation_typing": False,
            "binding": True,
            "embedsignature": True,
        },
        annotate=False,
    ),
)
