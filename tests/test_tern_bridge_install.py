import hashlib
import json

import pytest

from roxabi_sense.collectors.tern import bridge_install


def test_owned_copy_rejects_tampering_and_extra_files(tmp_path):
    for name in bridge_install._FILES:
        (tmp_path / name).write_text(name)
    hashes = {name: hashlib.sha256((tmp_path / name).read_bytes()).hexdigest()
              for name in bridge_install._FILES}
    (tmp_path / 'ownership.json').write_text(json.dumps({
        'schema': 1, 'id': 'roxabi-sense', 'hashes': hashes,
    }))
    assert bridge_install._owned(tmp_path)
    (tmp_path / 'window.luau').write_text('changed')
    assert not bridge_install._owned(tmp_path)


def test_reject_unowned_and_linked_id(tmp_path):
    (tmp_path / 'roxabi-sense').mkdir()
    with pytest.raises(ValueError):
        bridge_install._check_conflicts(tmp_path)
    (tmp_path / 'roxabi-sense').rmdir()
    (tmp_path / 'roxabi-sense.path').write_text('/other')
    with pytest.raises(ValueError):
        bridge_install._check_conflicts(tmp_path)
