"""Compatibility module; new code should import decode_easyae_dta."""

from decode_easyae_dta import (
    AEWIN_COLUMNS, DtaFormatError, DtaMetadata, Message, __version__,
    event_characteristics, iter_hits, iter_messages, iter_time_data,
    read_hits, read_metadata, read_time_data, setup_subrecords,
    write_hits_csv, write_time_csv, main,
)

if __name__ == "__main__":
    main()
