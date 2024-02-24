export const getSearchUrl = (org_id:string, query: string, search_uuid: string) => {
  const prefix = "/search";
  return `${prefix}?o=${org_id}&q=${encodeURIComponent(query)}&rid=${search_uuid}`;
};
