//! One vendor's GPU driver stack as data: the Linux kernel driver that binds
//! the PCI function, the Mesa Vulkan driver over it, and the few facts and
//! driver-internal calls Vulkan does not carry. Everything above and below
//! (the Linux environment, the DRM ioctl path, the Metal device, the AIR
//! compiler, the gate) is shared and reads a vendor only through this.
//!
//! [`GpuStack::select`] chooses by PCI vendor. Whether a device of that
//! vendor is driven is then the kernel driver's own ID table's answer, when
//! Linux matches the function against the registered driver.
use crate::{Adapter, Vendor};
use core::ffi::c_void;

/// A device-control call on the bound GPU's open DRM file: the request code
/// and its argument, answering Linux's result (zero or a negative errno).
pub type Control<'a> = &'a mut dyn FnMut(u32, *mut c_void) -> i32;

/// The Linux kernel driver of a stack.
pub struct Kernel {
    /// The name its `pci_driver` registers under.
    pub driver: &'static str,
    /// The memory BAR whose first page holds the GPU's registers.
    pub registers: usize,
    /// Whether a GPU is worth claiming for this driver in an image that
    /// links none of the stack's Vulkan driver: amdgpu brings the GPU up
    /// without Metal, while nouveau's GPUs serve only the Metal device.
    pub serves_without_vulkan: bool,
    /// The address bits of the widest DMA mask the driver sets on the devices
    /// Stela drives; a mapping past it is refused, not truncated.
    pub dma_bits: u8,
    /// The driver's own identifier of the bound chip, by its query ioctl.
    pub chip: fn(Control) -> Option<u32>,
    /// The bytes of device memory the driver makes available.
    pub memory_bytes: fn(Control) -> Option<u64>,
    /// For a driver whose recovery from a hung GPU holds back every later
    /// close of its files, how it tells whether it reset the GPU, so a GPU
    /// it reset during its first work is left unreleased.
    pub reset: Option<Reset>,
}

/// A kernel driver's watch for resets: a context on a file of its own,
/// made before the GPU's first work.
pub struct Reset {
    /// Makes the context, answering its identifier.
    pub watch: fn(Control) -> Option<u32>,
    /// Whether the driver reset the GPU since the context was made.
    pub state: fn(Control, u32) -> Option<ResetState>,
}

/// What a [`Reset`] watch saw.
#[derive(Clone, Copy, Debug, Eq, PartialEq)]
pub enum ResetState {
    /// No reset since the watch was made.
    Running,
    /// The driver reset the GPU, and the reset ended.
    Reset,
    /// The driver is resetting the GPU now.
    Resetting,
}

/// One vendor's stack.
pub struct GpuStack {
    pub vendor: Vendor,
    pub kernel: Kernel,
    /// Admission of the stack's Vulkan device as a Metal device.
    #[cfg(feature = "metal")]
    pub metal: crate::metal::open::Admission,
    /// The stack's Vulkan driver, when it is the one the image links: an
    /// image links one Mesa driver, whose runtime and the AIR compiler's
    /// units the others share. This crate's own test binary links no driver
    /// archive, so it holds none.
    #[cfg(all(feature = "metal-linked", not(test)))]
    pub vulkan: Option<crate::metal::native::Driver>,
}

impl Kernel {
    /// The widest DMA address the driver's mask allows.
    pub const fn dma_limit(&self) -> u64 {
        u64::MAX >> (64 - self.dma_bits)
    }
}

/// Every stack this build holds: amdgpu's only in an image built with it.
#[cfg(not(feature = "amdgpu-port"))]
pub static STACKS: [&GpuStack; 1] = [&NVIDIA];
/// Every stack this build holds: amdgpu's only in an image built with it.
#[cfg(feature = "amdgpu-port")]
pub static STACKS: [&GpuStack; 2] = [&NVIDIA, &AMD];

/// nouveau with GSP firmware, NVK and NAK.
pub static NVIDIA: GpuStack = GpuStack {
    vendor: Vendor::Nvidia,
    kernel: Kernel {
        driver: "nouveau",
        registers: 0,
        serves_without_vulkan: false,
        // The Turing-and-later MMUs' `dma_bits` (`nvkm/subdev/mmu/tu102.c`
        // 47, `gh100.c` 52), which `nvkm_device_pci_new` sets as the mask.
        dma_bits: 47,
        chip: crate::nouveau_chip::query::chipset,
        memory_bytes: crate::nouveau_chip::query::memory_bytes,
        reset: None,
    },
    #[cfg(feature = "metal")]
    metal: crate::metal::nvidia::ADMISSION,
    #[cfg(all(feature = "metal-linked", not(test)))]
    vulkan: crate::metal::nvidia::DRIVER,
};

/// amdgpu, with RADV and ACO over it for Metal.
pub static AMD: GpuStack = GpuStack {
    vendor: Vendor::Amd,
    kernel: Kernel {
        driver: "amdgpu",
        // From Bonaire on (`amdgpu_device_init`).
        registers: 5,
        serves_without_vulkan: true,
        // GMC 10 and later set 44 bits (`gmc_v10_0_sw_init`,
        // `gmc_v11_0_sw_init`, `gmc_v12_0_sw_init`).
        dma_bits: 44,
        chip: crate::amdgpu_chip::query::graphics_core,
        memory_bytes: crate::amdgpu_chip::query::memory_bytes,
        // A hung GPU's recovery holds back the VM work every later close of
        // a file waits for, which on Navi 21 has not been seen to end.
        reset: Some(Reset {
            watch: crate::amdgpu_chip::query::reset_watch,
            state: crate::amdgpu_chip::query::reset_state,
        }),
    },
    #[cfg(feature = "metal")]
    metal: crate::metal::amd::ADMISSION,
    #[cfg(all(feature = "metal-linked", not(test)))]
    vulkan: crate::metal::amd::DRIVER,
};

/// Brings Linux up and registers every kernel driver built into the image,
/// stopping at the first stage that fails. Linux then matches each presented
/// function against the registered drivers' own ID tables, so one image
/// serves every vendor's devices and registration is not per stack.
///
/// # Safety
/// On the canonical Linux owner's original managed task after Initial and
/// SRCU startup have completed, once per environment. Initcalls may sleep.
#[cfg(feature = "nouveau-port")]
pub unsafe fn start_kernel() -> Result<(), stela_linux::owner::Errno> {
    // SAFETY: the caller's contract.
    unsafe { crate::linux::start() }
}

impl GpuStack {
    /// Whether the image links the stack's Vulkan driver.
    pub fn links_vulkan(&self) -> bool {
        #[cfg(all(feature = "metal-linked", not(test)))]
        return self.vulkan.is_some();
        #[cfg(not(all(feature = "metal-linked", not(test))))]
        false
    }

    /// The stack for `adapter`'s vendor, or why Stela has none.
    pub fn select(adapter: &Adapter) -> Result<&'static GpuStack, &'static str> {
        STACKS
            .iter()
            .copied()
            .find(|stack| stack.vendor == adapter.vendor)
            .ok_or_else(|| adapter.unavailable_reason())
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::bus::pci::Address;

    fn adapter(vendor: u16) -> Adapter {
        Adapter {
            address: Address {
                segment: 0,
                bus: 1,
                device: 0,
                function: 0,
            },
            vendor: Vendor::from_pci(vendor),
            device: 0x1234,
            class: [3, 0, 0],
        }
    }

    #[test]
    fn a_stack_is_selected_by_pci_vendor_alone() {
        let nvidia = GpuStack::select(&adapter(0x10de)).unwrap();
        assert!(core::ptr::eq(nvidia, &NVIDIA));
        assert_eq!(nvidia.kernel.driver, "nouveau");
        let amd = GpuStack::select(&adapter(0x1002));
        if cfg!(feature = "amdgpu-port") {
            assert!(amd.is_ok_and(|amd| core::ptr::eq(amd, &AMD)));
        } else {
            assert_eq!(amd.err(), Some(adapter(0x1002).unavailable_reason()));
        }
        for vendor in [0x8086, 0x1234] {
            let adapter = adapter(vendor);
            assert_eq!(
                GpuStack::select(&adapter).err(),
                Some(adapter.unavailable_reason())
            );
        }
    }

    #[test]
    fn each_vendor_has_one_stack_and_a_usable_dma_mask() {
        for (index, stack) in STACKS.iter().enumerate() {
            assert!(
                STACKS[..index]
                    .iter()
                    .all(|other| other.vendor != stack.vendor)
            );
            assert!((32..=64).contains(&stack.kernel.dma_bits));
            assert_eq!(
                stack.kernel.dma_limit().count_ones(),
                stack.kernel.dma_bits.into()
            );
            assert!(!stack.kernel.driver.is_empty());
        }
    }

    #[test]
    fn nouveau_queries_ask_its_getparam_and_keep_refusals() {
        // `struct drm_nouveau_getparam`: the parameter, then its value.
        let mut asked = alloc::vec::Vec::new();
        let mut answer = |request: u32, argument: *mut c_void| {
            // SAFETY: the query passes its own two-word record.
            let words = unsafe { &mut *argument.cast::<[u64; 2]>() };
            asked.push((request, words[0]));
            words[1] = if words[0] == 11 { 0x1b3 } else { 16 << 30 };
            0
        };
        assert_eq!((NVIDIA.kernel.chip)(&mut answer), Some(0x1b3));
        assert_eq!((NVIDIA.kernel.memory_bytes)(&mut answer), Some(16 << 30));
        assert_eq!(asked, [(0xc010_6440, 11), (0xc010_6440, 8)]);
        let mut refuse = |_: u32, _: *mut c_void| -22;
        assert_eq!((NVIDIA.kernel.chip)(&mut refuse), None);
        assert_eq!((NVIDIA.kernel.memory_bytes)(&mut refuse), None);
        let mut empty = |_: u32, _: *mut c_void| 0;
        assert_eq!((NVIDIA.kernel.memory_bytes)(&mut empty), None);
    }

    #[test]
    fn amdgpu_queries_ask_its_info_and_keep_refusals() {
        // `struct drm_amdgpu_info`: the answer's address and size, then the
        // query.
        let mut asked = alloc::vec::Vec::new();
        let mut answer = |request: u32, argument: *mut c_void| {
            // SAFETY: the query passes its own record.
            let (pointer, size, query) = unsafe {
                let words = &*argument.cast::<[u32; 4]>();
                (
                    u64::from(words[0]) | u64::from(words[1]) << 32,
                    words[2],
                    words[3],
                )
            };
            asked.push((request, query, size));
            // SAFETY: the kernel writes the answer the query sized.
            let answer = unsafe { core::slice::from_raw_parts_mut(pointer as *mut u32, 8) };
            match query {
                // `ip_discovery_version` of `drm_amdgpu_info_hw_ip`.
                0x02 => answer[7] = 0x000a_0300,
                // `vram_size` of `drm_amdgpu_info_vram_gtt`.
                _ => answer[..2].copy_from_slice(&[0, 16]),
            }
            0
        };
        assert_eq!((AMD.kernel.chip)(&mut answer), Some(0x000a_0300));
        assert_eq!((AMD.kernel.memory_bytes)(&mut answer), Some(16 << 32));
        assert_eq!(asked, [(0x4020_6445, 0x02, 32), (0x4020_6445, 0x14, 24)]);
        let mut refuse = |_: u32, _: *mut c_void| -22;
        assert_eq!((AMD.kernel.chip)(&mut refuse), None);
        assert_eq!((AMD.kernel.memory_bytes)(&mut refuse), None);
        let mut empty = |_: u32, _: *mut c_void| 0;
        assert_eq!((AMD.kernel.chip)(&mut empty), None);
        assert_eq!((AMD.kernel.memory_bytes)(&mut empty), None);
    }

    #[test]
    fn amdgpu_watches_resets_on_a_context_of_its_own() {
        let reset = AMD.kernel.reset.as_ref().unwrap();
        assert!(NVIDIA.kernel.reset.is_none());
        // `union drm_amdgpu_ctx`: the operation, flags, context and priority
        // in; `alloc.ctx_id` or `state.flags` out over the first words.
        let mut asked = alloc::vec::Vec::new();
        let flags = core::cell::Cell::new(0u64);
        let mut answer = |request: u32, argument: *mut c_void| {
            // SAFETY: the query passes its own four-word record.
            let words = unsafe { &mut *argument.cast::<[u32; 4]>() };
            asked.push((request, words[0], words[2]));
            match words[0] {
                1 => words[0] = 7,
                _ => [words[0], words[1]] = [flags.get() as u32, (flags.get() >> 32) as u32],
            }
            0
        };
        assert_eq!((reset.watch)(&mut answer), Some(7));
        assert_eq!((reset.state)(&mut answer, 7), Some(ResetState::Running));
        flags.set(1);
        assert_eq!((reset.state)(&mut answer, 7), Some(ResetState::Reset));
        flags.set(1 | 1 << 5);
        assert_eq!((reset.state)(&mut answer, 7), Some(ResetState::Resetting));
        assert_eq!(asked[..2], [(0xc010_6442, 1, 0), (0xc010_6442, 4, 7)]);
        let mut refuse = |_: u32, _: *mut c_void| -22;
        assert_eq!((reset.watch)(&mut refuse), None);
        assert_eq!((reset.state)(&mut refuse, 7), None);
    }
}
