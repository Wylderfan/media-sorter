"""Named four-line source profiles; setup never invokes a transfer."""
import argparse
import os
import pathlib
import re
import sys
import tempfile


def profile_path(base, name, media):
    if not re.fullmatch(r'[a-zA-Z0-9][a-zA-Z0-9_-]*', name):
        raise ValueError('Profile name must use letters, digits, underscores or hyphens.')
    return pathlib.Path(str(base) + '.sources') / (name + '.' + media)


def media_profiles(base, media):
    default = pathlib.Path(str(base) + '.' + media)
    if media == 'movies' and not default.is_file():
        default = base
    if default.is_file():
        yield default
    yield from sorted(pathlib.Path(str(base) + '.sources').glob('*.' + media))


def main():
    parser = argparse.ArgumentParser(description='Create a new source profile without transferring media.')
    parser.parse_args()
    import tv
    if not sys.stdin.isatty():
        raise ValueError('Interactive input is required; setup cancelled.')
    base = pathlib.Path(os.environ.get('MEDIA_SORTER_CONFIG') or os.path.expanduser('~/.config/media-sorter/config'))
    media = tv.ask('Media type [movies/tv]: ')
    if media not in ('movies', 'tv'):
        raise ValueError('Supported media types: movies, tv.')
    name = tv.ask('Profile name: ')
    config = profile_path(base, name, media)
    if os.path.lexists(config):
        raise ValueError('Configuration already exists; nothing overwritten: ' + tv.display_path(config))
    source = tv.ask('Absolute source directory: ')
    host = tv.ask('Server hostname or SSH alias: ')
    user = tv.ask('SSH username: ')
    dest = tv.ask('Absolute destination directory: ')
    tv.absolute(source)
    tv.absolute(dest)
    if not pathlib.Path(source).is_dir() or not os.access(source, os.R_OK):
        raise ValueError('Source must be a readable directory.')
    if media == 'tv':
        tv.real_directory(pathlib.Path(source))
    if not re.fullmatch(r'[a-zA-Z0-9][a-zA-Z0-9._-]*', host) or not re.fullmatch(r'[a-zA-Z_][a-zA-Z0-9_-]*', user):
        raise ValueError('Invalid SSH host/user.')
    other_media = 'tv' if media == 'movies' else 'movies'
    for other in media_profiles(base, other_media):
        other_source, other_host, _, other_dest = tv.read_profile(other)
        if tv.overlap(source, tv.absolute(other_source)) or (host == other_host and tv.overlap(dest, tv.absolute(other_dest))):
            raise ValueError('Movie and TV roots overlap; choose independent roots.')
    print('Media:', media)
    print('Configuration:', tv.display_path(config))
    print('Local source:', tv.display_path(source))
    print('Remote library:', tv.display_path(user + '@' + host + ':' + dest))
    print('Setup does not contact the server or transfer media.')
    if tv.ask('Create source profile? [yes/no] ') != 'yes':
        raise ValueError('Setup cancelled.')
    config.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix='.new-', dir=config.parent)
    try:
        with os.fdopen(fd, 'w') as stream:
            stream.write('\n'.join((source, host, user, dest)) + '\n')
        # Publish the complete mode-0600 file exclusively, even if another setup
        # created the target since the initial check. Never follow/replace it.
        os.link(temporary, config)
    finally:
        os.unlink(temporary)
    print('Saved', tv.display_path(config))
    print('Preview with: media-sorter ' + media + ' --profile ' + name)
    return 0


if __name__ == '__main__':
    try:
        sys.exit(main())
    except (OSError, ValueError, KeyboardInterrupt) as error:
        print('Error:', error, file=sys.stderr)
        sys.exit(1)
