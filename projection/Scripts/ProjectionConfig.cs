/// <summary>
/// Configuration for the projection player — the Unity-side equivalent of the
/// server's config.yaml. Every port, path and timing lives here so that moving
/// the installation, re-exporting a clip, or retiming a fade is a file edit and
/// never a rebuild (CLAUDE.md: "config, not code").
///
/// Loaded from a JSON file found next to the built application. Fail-soft: a
/// missing or malformed config logs loudly and falls back to these defaults
/// rather than refusing to start — an exhibition machine must always come up.
/// </summary>
using System;
using System.IO;
using UnityEngine;

namespace Coral
{
    /// <summary>
    /// Three looping state clips plus one short one-shot for the bleach latch.
    /// There are no directional transition clips: every other change is an opacity
    /// blend driven by the broadcast intensity.
    /// </summary>
    [Serializable]
    public class ClipPaths
    {
        public string healthy = "coral-healthy.mov";
        public string fluorescent = "coral-fluorescent.mov";
        public string bleached = "coral-bleached.mov";
        public string latch = "coral-fluorescent-to-bleached.mov";
    }

    [Serializable]
    public class ProjectionConfig
    {
        public int osc_port = 9020;
        // "video" matches the portable bundle layout (.app + config + video/), so
        // the built-in defaults resolve to real clips when a config is missing or
        // corrupt rather than to a black screen.
        public string video_dir = "video";
        public ClipPaths clips = new ClipPaths();
        public float crossfade_s = 1.5f;

        // Source frame size of the exported clips. Must be stated rather than
        // detected, because the RenderTextures are built before any clip is
        // prepared — and if a RenderTexture's aspect disagrees with the video's,
        // Unity squashes the frame into it before the compositor ever sees it.
        // The player logs a warning if a prepared clip disagrees with these.
        public int video_width = 2560;
        public int video_height = 800;

        // The whole spanned canvas, across every projector. Not the size of one
        // window: the player composites at this size and each output takes its own
        // horizontal slice of it.
        public int display_width = 2560;
        public int display_height = 800;
        public bool fullscreen = true;

        // Unity display indices, LEFT TO RIGHT, one per projector. Index 0 is always
        // whichever screen macOS calls the Main Display, so make the left projector
        // main (System Settings > Displays, drag the menu bar onto it) or list the
        // indices the player logs at startup. An index that is not attached is
        // skipped with a warning, so a laptop-only bench run falls back to a single
        // output showing the whole canvas.
        public int[] displays = { 0, 1 };

        // How a source frame whose aspect differs from the canvas is mapped:
        //   "cover"   fill the canvas, cropping the overflow (no distortion)
        //   "contain" fit the whole frame, letterboxing the remainder
        //   "stretch" force it to fit (distorts — only for deliberate anamorphic)
        public string fit = "cover";

        // Which part survives a "cover" crop, -1..+1, 0 = centred. If it pans the
        // wrong way, flip the sign — texture V orientation varies by platform.
        public float pan_x = 0f;
        public float pan_y = 0f;

        public bool log_transitions = true;

        /// <summary>Directory the config was loaded from; clips resolve against it.</summary>
        [NonSerialized] public string SourceDir = "";

        const string FileName = "coral-projection.json";

        /// <summary>
        /// Locate and parse the config. Search order: an explicit
        /// <c>--config &lt;path&gt;</c> command-line argument, then upward from the
        /// player's data directory (which covers both a .app bundle and a plain
        /// folder build), then StreamingAssets as the in-editor fallback.
        /// </summary>
        public static ProjectionConfig Load()
        {
            string path = Resolve();
            if (path == null)
            {
                Debug.LogWarning($"[config] no {FileName} found — using built-in defaults");
                return new ProjectionConfig();
            }

            try
            {
                var cfg = JsonUtility.FromJson<ProjectionConfig>(File.ReadAllText(path));
                if (cfg == null) throw new Exception("parsed to null");
                cfg.SourceDir = Path.GetDirectoryName(path);
                Debug.Log($"[config] loaded {path}");
                return cfg;
            }
            catch (Exception e)
            {
                // Malformed JSON must not stop the installation from booting. Keep
                // SourceDir even so: without it the default clip names resolve to
                // bare filenames against the process working directory, which never
                // matches, so every layer fails to open and the fallback can only
                // ever show black. With it, a corrupt config still finds the clips
                // sitting beside it and the piece runs on defaults.
                Debug.LogError($"[config] failed to parse {path} ({e.Message}) — using defaults");
                return new ProjectionConfig { SourceDir = Path.GetDirectoryName(path) };
            }
        }

        static string Resolve()
        {
            foreach (var arg in ArgPath()) if (arg != null && File.Exists(arg)) return arg;

            // Walk up from Application.dataPath. A macOS build puts dataPath at
            // Coral.app/Contents/Resources/Data, so the config sitting beside the
            // bundle is four levels up; a folder build is one. Search rather than
            // hard-code the depth so both layouts work unchanged.
            var dir = new DirectoryInfo(Application.dataPath);
            for (int i = 0; i < 6 && dir != null; i++, dir = dir.Parent)
            {
                string candidate = Path.Combine(dir.FullName, FileName);
                if (File.Exists(candidate)) return candidate;
            }

            string streaming = Path.Combine(Application.streamingAssetsPath, FileName);
            return File.Exists(streaming) ? streaming : null;
        }

        static string[] ArgPath()
        {
            var args = Environment.GetCommandLineArgs();
            for (int i = 0; i < args.Length - 1; i++)
                if (args[i] == "--config") return new[] { args[i + 1] };
            return Array.Empty<string>();
        }

        /// <summary>
        /// Absolute path for a clip, or null if unset. A relative <c>video_dir</c>
        /// resolves against the config file's own directory, not the process
        /// working directory — that is what lets the whole thing be a portable
        /// bundle (.app + config + video/ folder) that works wherever it is copied,
        /// including onto the cold-restore USB.
        /// </summary>
        public string ResolveClip(string clip)
        {
            if (string.IsNullOrWhiteSpace(clip)) return null;
            if (Path.IsPathRooted(clip)) return clip;

            string baseDir = SourceDir;
            if (!string.IsNullOrWhiteSpace(video_dir))
            {
                baseDir = Path.IsPathRooted(video_dir)
                    ? video_dir
                    : Path.Combine(SourceDir ?? "", video_dir);
            }
            return string.IsNullOrEmpty(baseDir) ? clip : Path.GetFullPath(Path.Combine(baseDir, clip));
        }
    }
}
