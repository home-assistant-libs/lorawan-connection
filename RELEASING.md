# Releasing

This repository uses the same release-tag versioning and PyPI Trusted Publishing
workflow as `modbus-connection`. No PyPI token belongs in this repository.

## Publisher configuration

PyPI Trusted Publishing uses these values:

| Setting | Value |
| --- | --- |
| PyPI project name | `lorawan-connection` |
| GitHub owner | `home-assistant-libs` |
| GitHub repository | `lorawan-connection` |
| Workflow filename | `publish.yml` |
| Environment | `pypi` |

GitHub Pages uses the `github-pages` environment and the Actions build source.
Its site is <https://home-assistant-libs.github.io/lorawan-connection/>.

## Publish a release

1. Check CI and the documentation build for the release commit.
2. Confirm that the PyPI publisher configuration matches the table above.
3. Publish the GitHub release with tag `0.9.0`. Use a numeric tag without a `v` prefix.
4. The workflow replaces source version `0.0.0` with the tag and builds a wheel and sdist.
5. The `pypi` job publishes those artifacts with GitHub's OIDC identity.
6. Verify installation with `uv run --with lorawan-connection==0.9.0 --no-project python -c 'import lorawan_connection'`.

A draft release does not trigger publication. The workflow also supports manual dispatch with an
existing tag to retry a release that failed before uploading artifacts to PyPI.

## Build release artifacts locally

```sh
uv run python script/build_release.py 0.9.0
```

The script builds from an isolated copy and leaves `pyproject.toml` unchanged.
It validates a numeric three-part release version. Artifacts appear in `dist/`.
The release workflow uses the same script.
