# Altx-cpp

## Development

Start an interactive development session with the repository mounted:

```bash
docker run --rm -it -v "$PWD:/workspace" -w /workspace \
    ghcr.io/intensivedatacomp/altx-cpp/dev-cpu:edge
```

`edge` is the newest build of the development image, pulled automatically on first use.

To build the image yourself instead — which is also how you get one matching your own uid — use
`./scripts/build_docker_images_locally.sh --target dev` and run `altx-cpp/dev-cpu:local`.

See [docs/DevelopmentEnvironment.md](docs/DevelopmentEnvironment.md) for the other configurations
and the editor tooling.
