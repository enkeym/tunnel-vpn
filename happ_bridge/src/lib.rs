use std::ffi::{CStr, CString};
use std::os::raw::c_char;

#[no_mangle]
pub unsafe extern "C" fn happ_decrypt(input: *const c_char) -> *mut c_char {
    if input.is_null() {
        return std::ptr::null_mut();
    }
    let result = std::panic::catch_unwind(|| {
        let text = CStr::from_ptr(input).to_str().ok()?;
        let value = happwn::decrypt(text).ok()?.value;
        CString::new(value).ok().map(CString::into_raw)
    });
    result.ok().flatten().unwrap_or(std::ptr::null_mut())
}

#[no_mangle]
pub unsafe extern "C" fn happ_free(value: *mut c_char) {
    if !value.is_null() {
        drop(CString::from_raw(value));
    }
}
