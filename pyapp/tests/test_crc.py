from openinverter_web.flasher import crc32_chunk, crc32_word


def test_stm32_crc_known_vectors():
    # STM32 hardware CRC unit: reset value 0xFFFFFFFF; feeding 0x00000000
    # yields 0xC704DD7B (documented behavior), feeding 0xFFFFFFFF yields 0.
    assert crc32_word(0xFFFFFFFF, 0x00000000) == 0xC704DD7B
    assert crc32_word(0xFFFFFFFF, 0xFFFFFFFF) == 0x00000000


def test_chunk_folds_two_little_endian_words():
    chunk = bytes(range(8))
    w0 = int.from_bytes(chunk[0:4], "little")
    w1 = int.from_bytes(chunk[4:8], "little")
    assert crc32_chunk(0xFFFFFFFF, chunk) == crc32_word(crc32_word(0xFFFFFFFF, w0), w1)
