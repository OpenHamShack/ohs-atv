/* OpenHamShack ATV's cross-check against hacktv's teletext inserter.
 *
 * Built against hacktv's own src/teletext.c (fsphil/hacktv, GPLv3), which it
 * #includes so that hacktv's static functions run unchanged: _load_tti for
 * each .tti given, then _next_packet for N packets, written as a .t42 (each
 * 45-byte line less its clock run-in and framing code, which are checked).
 * The clock is fixed so the headers and their CRC are reproducible; hacktv's
 * line renderer is stubbed out (only the packets are wanted).
 *
 *   cc -I<hacktv>/src -o hacktv-drv hacktv-drv.c -lm
 *   TZ=UTC ./hacktv-drv out.t42 N page.tti [page.tti ...]
 */
#include "teletext.c"

vbidata_lut_t *vbidata_init(unsigned int nsymbols, unsigned int dwidth, int level, int filter, double bwidth, double beta, double offset) { return NULL; }
void vbidata_render(const vbidata_lut_t *lut, const uint8_t *src, int offset, int length, int order, vid_line_t *line) { }

time_t time(time_t *t) { if (t) *t = 1759680000; return 1759680000; }	/* 2025-10-05 16:00:00 UTC */

int main(int argc, char **argv)
{
	tt_service_t s;
	uint8_t line[45];
	int i, n;
	unsigned int tc = 0;
	FILE *out;
	
	if(argc < 4) return(2);
	setenv("TZ", "UTC", 1);
	tzset();
	n = atoi(argv[2]);
	_new_service(&s);
	for(i = 3; i < argc; i++)
	{
		if(_load_tti(&s, argv[i]) != TT_OK) return(1);
	}
	out = fopen(argv[1], "wb");
	if(!out) return(1);
	/* One packet a frame: hacktv's 20 ms page-erasure wait is always over. */
	for(i = 0; i < n; i++, tc += 625)
	{
		if(_next_packet(&s, line, tc) != TT_OK) continue;
		if(line[0] != 0x55 || line[1] != 0x55 || line[2] != 0x27) return(3);
		fwrite(&line[3], 1, 42, out);
	}
	fclose(out);
	return(0);
}
