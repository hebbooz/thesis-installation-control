/// <summary>
/// One projector. macOS does not join two displays into a single desktop, so a
/// single fullscreen window can never cover both — instead the player composites
/// the whole spanned canvas once into a RenderTexture and each CoralOutput blits
/// its own horizontal slice of that canvas to its own display.
///
/// Every output reads the SAME canvas in the same frame, so the two halves can
/// never drift: there is one set of VideoPlayers, one blend, one playhead per
/// clip. (Running two copies of the player, one per projector, would decode
/// independently and the caustics would slide out of phase across the seam.)
///
/// The camera draws nothing itself — culling mask 0, cleared to black. It exists
/// only so <c>OnRenderImage</c> fires on the right display.
/// </summary>
using UnityEngine;

namespace Coral
{
    [RequireComponent(typeof(Camera))]
    public class CoralOutput : MonoBehaviour
    {
        ProjectionPlayer _player;
        Vector2 _scale = Vector2.one;
        Vector2 _offset = Vector2.zero;

        /// <summary>
        /// Claim slice <paramref name="index"/> of <paramref name="count"/>, left to
        /// right. With one output the slice is the whole canvas, which is what makes
        /// the windowed single-screen bench setup work unchanged.
        /// </summary>
        public void Bind(ProjectionPlayer player, int index, int count)
        {
            _player = player;
            int n = Mathf.Max(count, 1);
            _scale = new Vector2(1f / n, 1f);
            _offset = new Vector2((float)index / n, 0f);
        }

        void OnRenderImage(RenderTexture src, RenderTexture dst)
        {
            var canvas = _player != null ? _player.Canvas : null;

            // Before the first composite, or if the player failed to build a canvas:
            // pass the camera's own cleared black through rather than showing junk.
            if (canvas == null) { Graphics.Blit(src, dst); return; }

            Graphics.Blit(canvas, dst, _scale, _offset);
        }
    }
}
