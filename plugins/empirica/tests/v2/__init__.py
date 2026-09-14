"""Empirica 2.0 D4 red-first behavioral conformance tests.

Black-box tests bound to the real ``empirica/v2`` SUT seam through ``driver.new_driver``. They import
no core policy function and reimplement no runtime algorithm. In the current pre-D6/D7 tree the v2
composition seam is absent, so ``new_driver`` raises :class:`driver.V2SeamAbsent`` and every case
fails (as a FAILURE, not a collection ERROR) with its owner + expected behavior. When D6/D7 introduce
the seam, the same assertions exercise the real v2 behavior.
"""
