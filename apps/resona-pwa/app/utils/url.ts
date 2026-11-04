/** Strip trailing slashes so `${base}/path` never doubles them. */
export function trimUrl(url: string): string {
	return url.replace(/\/+$/, "");
}
