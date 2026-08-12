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

## Contributing

1. Make sure the test suite and pre-commit hooks pass on your branch before
   opening a pull request.
2. Follow the [NumPy docstring convention](https://numpydoc.readthedocs.io/en/latest/format.html).
3. Keep each pull request focused on a single change.

```bash
git switch -c feature/your-awesome-feature

# ... make changes ...

git add .
git commit -m "Useful commit message"
git push --set-upstream origin feature/your-awesome-feature
xdg-open https://github.com/dcintlab/artificial-dataset/pull/new/feature/your-awesome-feature

# ... merge the branch to main, make sure that the pipeline passes, delete the branch ...

git switch main
git pull
git branch -d feature/your-awesome-feature
git branch -d feature/your-awesome-feature --remote
```

## License

This project is licensed under the [GNU General Public License v3.0](LICENSE).