# Altx-cpp

## Development

Start an interactive development session with the repository mounted:

```bash
docker run --rm -it -v "$PWD:/workspace" -w /workspace altx-cpp/dev-cpu:local
```

The image is built locally with `./scripts/build_docker_images_locally.sh --target dev`. Once CI
publishes it, `altx-cpp/dev-cpu:local` is replaced by `ghcr.io/intensivedatacomp/altx-cpp/dev-cpu:edge`.

See [docs/DevelopmentEnvironment.md](docs/DevelopmentEnvironment.md) for the other configurations
and the editor tooling.
