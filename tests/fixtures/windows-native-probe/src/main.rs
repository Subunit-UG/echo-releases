use std::ffi::CStr;

fn main() {
    let languages = unsafe { whisper_rs_sys::whisper_lang_max_id() } + 1;
    let opus = unsafe { CStr::from_ptr(opusic_sys::opus_get_version_string()) };
    println!("whisper languages: {languages}");
    println!("opus: {}", opus.to_string_lossy());
    // Linked always; only enumerated on request, since hosted runners have no GPU.
    #[cfg(feature = "gpu")]
    if std::env::var_os("PROBE_LIST_VULKAN_DEVICES").is_some() {
        println!("vulkan devices: {}", whisper_rs::vulkan::list_devices().len());
    }
}
