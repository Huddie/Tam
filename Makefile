.PHONY: build publish version clean publish-discovery publish-data-explorer publish-sites

# Bumps pyproject.toml's version to the next patch release above whatever's
# currently live on PyPI (queries the index; falls back to the existing
# pyproject.toml version if the package has never been published), then
# builds sdist+wheel and uploads to public PyPI via `uv publish`. Requires
# UV_PUBLISH_TOKEN (a PyPI API token) in the environment -- uv reads it
# directly, nothing here handles or echoes it.
#
# --publish-url is explicit, not left to uv's default -- this repo's
# pyproject.toml points [[tool.uv.index]] at pypi.apple.com (an internal
# mirror, used for dependency resolution during development), and `uv
# publish` on its own resolves the same configured index first. Pinning the
# real public endpoint here means a publish always goes to public PyPI
# regardless of whatever index config this repo carries for other purposes.
publish: version build
	uv publish --publish-url https://upload.pypi.org/legacy/

version:
	uv run python scripts/bump_version.py

build: clean
	uv build

clean:
	rm -rf dist build *.egg-info

# Manual, local equivalent of .github/workflows/deploy-discovery.yml/
# deploy-data-explorer.yml's own deploy step -- same `npm run deploy`
# (vite build && wrangler deploy) either one runs in CI, just triggered by
# hand instead of a push to main. Needs `npx wrangler login` done at least
# once on this machine (see each site's own README.md for the full
# Cloudflare setup runbook).
publish-discovery:
	cd tam-discovery && npm run deploy

publish-data-explorer:
	cd tam-data-explorer && npm run deploy

publish-sites: publish-discovery publish-data-explorer
