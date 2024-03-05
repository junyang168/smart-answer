export const getSearchUrl = (org_id:string, query: string, search_uuid: string) => {
  const prefix = "/search";
  return `${prefix}?o=${org_id}&q=${encodeURIComponent(query)}&rid=${search_uuid}`;
};

export const getSearchHome = (org_id:string, search_uuid: string) => {
  return `/?o=${org_id}&rid=${search_uuid}`;
};
