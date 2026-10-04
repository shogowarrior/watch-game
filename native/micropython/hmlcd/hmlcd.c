// hmlcd: push RGB565 frames to the T-Watch's ST7789 from a task on core 0.
//
//     import hmlcd
//     spi.deinit()                       # machine.SPI(1) gives the bus back first
//     hmlcd.init(40_000_000)             # host=1 (HSPI), sck=18, mosi=19, cs=5, dc=27
//     hmlcd.push(frame, 0, 240)          # returns at once; frame stays untouched until
//     hmlcd.wait()                       # wait(), the next push() or cmd() returns
//     hmlcd.stats()                      # (pushes, last_us, max_us, total_us, waited_us)
//
// The panel must already be set up (hal/st7789.py's init over machine.SPI);
// hmlcd sends windows and pixels only, half-duplex so clocks past 26.67 MHz work.
#include "py/mpthread.h"
#include "py/runtime.h"
#include "pusher.h"

#define W (240)

MP_REGISTER_ROOT_POINTER(mp_obj_t hmlcd_buf);   // keeps the buffer of a running push alive

static void check(int err) {
    // MP_ERROR_TEXT needs literals (the port compresses error strings).
    switch (err) {
        case HMLCD_OK:
            return;
        case HMLCD_NOMEM:
            mp_raise_msg(&mp_type_OSError, MP_ERROR_TEXT("no internal DMA memory"));
        case HMLCD_BUS:
            mp_raise_msg(&mp_type_OSError, MP_ERROR_TEXT("SPI host in use (deinit machine.SPI first)"));
        case HMLCD_CLOCK:
            mp_raise_msg(&mp_type_OSError, MP_ERROR_TEXT("SPI clock not available"));
        case HMLCD_TASK:
            mp_raise_msg(&mp_type_OSError, MP_ERROR_TEXT("no task"));
        default:
            mp_raise_msg(&mp_type_OSError, MP_ERROR_TEXT("not started (call hmlcd.init)"));
    }
}

static void started(void) {
    check(hmlcd_started() ? HMLCD_OK : HMLCD_STOPPED);
}

static mp_obj_t hmlcd_init(size_t n_args, const mp_obj_t *pos_args, mp_map_t *kw_args) {
    enum { ARG_baudrate, ARG_host, ARG_sck, ARG_mosi, ARG_cs, ARG_dc };
    static const mp_arg_t allowed[] = {
        { MP_QSTR_baudrate, MP_ARG_INT, {.u_int = 26666666} },
        { MP_QSTR_host, MP_ARG_KW_ONLY | MP_ARG_INT, {.u_int = 1} },
        { MP_QSTR_sck, MP_ARG_KW_ONLY | MP_ARG_INT, {.u_int = 18} },
        { MP_QSTR_mosi, MP_ARG_KW_ONLY | MP_ARG_INT, {.u_int = 19} },
        { MP_QSTR_cs, MP_ARG_KW_ONLY | MP_ARG_INT, {.u_int = 5} },
        { MP_QSTR_dc, MP_ARG_KW_ONLY | MP_ARG_INT, {.u_int = 27} },
    };
    mp_arg_val_t a[MP_ARRAY_SIZE(allowed)];
    mp_arg_parse_all(n_args, pos_args, kw_args, MP_ARRAY_SIZE(allowed), allowed, a);
    MP_THREAD_GIL_EXIT();
    int err = hmlcd_start(a[ARG_host].u_int, a[ARG_sck].u_int, a[ARG_mosi].u_int, a[ARG_cs].u_int,
        a[ARG_dc].u_int, a[ARG_baudrate].u_int);
    MP_THREAD_GIL_ENTER();
    MP_STATE_PORT(hmlcd_buf) = MP_OBJ_NULL;
    check(err);
    return mp_const_none;
}
static MP_DEFINE_CONST_FUN_OBJ_KW(hmlcd_init_obj, 0, hmlcd_init);

static mp_obj_t hmlcd_deinit(void) {
    MP_THREAD_GIL_EXIT();
    hmlcd_stop();
    MP_THREAD_GIL_ENTER();
    MP_STATE_PORT(hmlcd_buf) = MP_OBJ_NULL;
    return mp_const_none;
}
static MP_DEFINE_CONST_FUN_OBJ_0(hmlcd_deinit_obj, hmlcd_deinit);

static mp_obj_t hmlcd_baudrate(mp_obj_t hz) {
    started();
    uint32_t h = mp_obj_get_int(hz);
    MP_THREAD_GIL_EXIT();
    int err = hmlcd_set_clock(h);
    MP_THREAD_GIL_ENTER();
    check(err);
    return mp_const_none;
}
static MP_DEFINE_CONST_FUN_OBJ_1(hmlcd_baudrate_obj, hmlcd_baudrate);

static mp_obj_t hmlcd_cmd(size_t n_args, const mp_obj_t *args) {
    started();
    uint8_t c = mp_obj_get_int(args[0]);
    mp_buffer_info_t bi = { .buf = NULL, .len = 0 };
    if (n_args > 1 && args[1] != mp_const_none) {
        mp_get_buffer_raise(args[1], &bi, MP_BUFFER_READ);
    }
    MP_THREAD_GIL_EXIT();
    hmlcd_command(c, bi.buf, bi.len);
    MP_THREAD_GIL_ENTER();
    return mp_const_none;
}
static MP_DEFINE_CONST_FUN_OBJ_VAR_BETWEEN(hmlcd_cmd_obj, 1, 2, hmlcd_cmd);

static mp_obj_t hmlcd_push_(size_t n_args, const mp_obj_t *args) {
    started();
    mp_buffer_info_t bi;
    mp_get_buffer_raise(args[0], &bi, MP_BUFFER_READ);
    mp_int_t y0 = n_args > 1 ? mp_obj_get_int(args[1]) : 0;
    mp_int_t rows = n_args > 2 ? mp_obj_get_int(args[2]) : W;
    if (y0 < 0 || rows <= 0 || y0 + rows > W || bi.len < (size_t)(W * rows * 2)) {
        mp_raise_ValueError(MP_ERROR_TEXT("need rows*480 bytes for rows y0..y0+rows-1 of 240"));
    }
    MP_THREAD_GIL_EXIT();
    hmlcd_push((const uint16_t *)bi.buf, y0, rows);   // waits for the previous push first
    MP_THREAD_GIL_ENTER();
    MP_STATE_PORT(hmlcd_buf) = args[0];
    return mp_const_none;
}
static MP_DEFINE_CONST_FUN_OBJ_VAR_BETWEEN(hmlcd_push_obj, 1, 3, hmlcd_push_);

static mp_obj_t hmlcd_wait_(void) {
    started();
    MP_THREAD_GIL_EXIT();
    hmlcd_wait();
    MP_THREAD_GIL_ENTER();
    MP_STATE_PORT(hmlcd_buf) = MP_OBJ_NULL;
    return mp_const_none;
}
static MP_DEFINE_CONST_FUN_OBJ_0(hmlcd_wait_obj, hmlcd_wait_);

static mp_obj_t hmlcd_busy_(void) {
    return mp_obj_new_bool(hmlcd_started() && hmlcd_busy());
}
static MP_DEFINE_CONST_FUN_OBJ_0(hmlcd_busy_obj, hmlcd_busy_);

static mp_obj_t hmlcd_stats_(void) {
    uint32_t s[5] = {0, 0, 0, 0, 0};
    if (hmlcd_started()) {
        hmlcd_stats(s);
    }
    mp_obj_t t[5];
    for (int i = 0; i < 5; i++) {
        t[i] = mp_obj_new_int_from_uint(s[i]);
    }
    return mp_obj_new_tuple(5, t);
}
static MP_DEFINE_CONST_FUN_OBJ_0(hmlcd_stats_obj, hmlcd_stats_);

static const mp_rom_map_elem_t hmlcd_globals_table[] = {
    { MP_ROM_QSTR(MP_QSTR___name__), MP_ROM_QSTR(MP_QSTR_hmlcd) },
    { MP_ROM_QSTR(MP_QSTR_init), MP_ROM_PTR(&hmlcd_init_obj) },
    { MP_ROM_QSTR(MP_QSTR_deinit), MP_ROM_PTR(&hmlcd_deinit_obj) },
    { MP_ROM_QSTR(MP_QSTR_baudrate), MP_ROM_PTR(&hmlcd_baudrate_obj) },
    { MP_ROM_QSTR(MP_QSTR_cmd), MP_ROM_PTR(&hmlcd_cmd_obj) },
    { MP_ROM_QSTR(MP_QSTR_push), MP_ROM_PTR(&hmlcd_push_obj) },
    { MP_ROM_QSTR(MP_QSTR_wait), MP_ROM_PTR(&hmlcd_wait_obj) },
    { MP_ROM_QSTR(MP_QSTR_busy), MP_ROM_PTR(&hmlcd_busy_obj) },
    { MP_ROM_QSTR(MP_QSTR_stats), MP_ROM_PTR(&hmlcd_stats_obj) },
};
static MP_DEFINE_CONST_DICT(hmlcd_globals, hmlcd_globals_table);

const mp_obj_module_t hmlcd_user_cmodule = {
    .base = { &mp_type_module },
    .globals = (mp_obj_dict_t *)&hmlcd_globals,
};

MP_REGISTER_MODULE(MP_QSTR_hmlcd, hmlcd_user_cmodule);
