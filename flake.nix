{
  description = "fit4 dev shell and cc-job image: Python, uv, and an FFmpeg with libx264, libvpx-vp9 and xpsnr";

  inputs.nixpkgs.url = "github:NixOS/nixpkgs/nixos-unstable";

  outputs = { nixpkgs, ... }:
    let
      system = "x86_64-linux";
      pkgs = nixpkgs.legacyPackages.${system};
      # Matches .python-version, so uv finds it and never downloads one.
      # rich is fit4's only runtime dependency; pytest lets a cc-job run the
      # tests with --net none. Both are baked into the image.
      python = pkgs.python312.withPackages (ps: [ ps.rich ps.pytest ]);
    in
    {
      # Also built into an offline batch image (packages only, no shellHook,
      # sources copied to /w): run `python -m fit4 ...` there rather than
      # `uv run`, which would try to sync from PyPI. ccJobEnv sets that
      # image's extra environment.
      devShells.${system}.default = pkgs.mkShellNoCC {
        packages = [
          python
          pkgs.uv
          # headless has libx264, libvpx-vp9, libopus, aac, libwebp and the
          # xpsnr filter at a third of ffmpeg-full's closure.
          pkgs.ffmpeg-headless
        ];
        passthru.ccJobEnv = {
          PYTHONPATH = "/w/src";
          UV_PYTHON_DOWNLOADS = "never";
        };
      };
    };
}
