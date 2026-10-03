import producer as cli


def parse(*argv):
    return cli.build_config(cli.build_parser().parse_args(list(argv)))


def test_defaults():
    c = parse()
    assert c.modalities == ('video',)
    assert c.partition is None
    assert c.realtime_pacing and not c.stop_on_end and not c.strict_modalities


def test_full_args():
    c = parse('--source', '0', '--modalities', 'video, audio', '--audio-device', '2',
              '--frame-rate', '15', '--partition', '1', '--no-pacing', '--stop-on-end', '--strict',
              '--events-topic', 'ev')
    assert c.source == 0 and c.audio_device == 2
    assert c.modalities == ('video', 'audio')
    assert c.frame_rate == 15 and c.partition == 1
    assert not c.realtime_pacing and c.stop_on_end and c.strict_modalities
    assert c.events_topic == 'ev'


def test_string_sources_kept():
    assert parse('--source', 'mic').source == 'mic'
    assert parse('--source', 'rtsp://h/s', '--audio-device', 'USB Mic').audio_device == 'USB Mic'


def test_invalid_config_returns_error_code():
    assert cli.main(['--modalities', 'image']) == 2
