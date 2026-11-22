use std::ffi::CStr;

fn main() {
    let languages = unsafe { whisper_rs_sys::whisper_lang_max_id() } + 1;
    let opus = unsafe { CStr::from_ptr(opusic_sys::opus_get_version_string()) };
    println!("whisper languages: {languages}");
    println!("opus: {}", opus.to_string_lossy());
    #[cfg(feature = "gpu")]
    println!("vulkan devices: {}", whisper_rs::vulkan::list_devices().len());
}
