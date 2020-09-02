# -*- coding: utf-8 -*-
import pytest

from sphinx_js.suffix_tree import SuffixAmbiguous, SuffixNotFound, SuffixTree


def test_things():
    s = SuffixTree()
    s.add(['./', 'dir/', 'utils.', 'max'], 1)
    s.add(['./', 'dir/', 'footils.', 'max'], 2)
    s.add(['./', 'dir/', 'footils.', 'hacks'], 3)

    assert s.get(['hacks']) == 3
    assert s.get(['footils.', 'max']) == 2
    with pytest.raises(SuffixNotFound):
        s.get(['quacks.', 'max'])
    with pytest.raises(SuffixAmbiguous):
        s.get(['max'])


def test_full_path():
    """Looking up a full path should not crash."""
    s = SuffixTree()
    s.add(['./', 'dir/', 'footils.', 'jacks'], 4)
    assert s.get(['./', 'dir/', 'footils.', 'jacks']) == 4
