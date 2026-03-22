from bridge_core.stream.profiles import negotiate_stream_profile, is_profile_supported, STREAM_PROFILES
from ingress_sdk.types import SourceCapabilities

class MockTargetDescriptor:
    def __init__(self, tid, codecs=None):
        self.target_id = tid
        self.supported_codecs = codecs or ["mp3", "aac", "pcm_s16le"]
        self.supported_sample_rates = [48000]
        self.supported_channels = [2]
        self.max_bitrate_kbps = None

source_caps = SourceCapabilities(codecs=["pcm_s16le", "mp3", "aac"])
target_caps = MockTargetDescriptor("tgt_1")

print(f"PCM supported: {is_profile_supported('pcm_wav_48k_stereo_16', source_caps, target_caps)}")
print(f"AAC supported: {is_profile_supported('aac_48k_stereo_256', source_caps, target_caps)}")
print(f"MP3 supported: {is_profile_supported('mp3_48k_stereo_320', source_caps, target_caps)}")

negotiated = negotiate_stream_profile(source_caps, target_caps)
print(f"Negotiated: {negotiated}")
