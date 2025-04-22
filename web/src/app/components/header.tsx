import React, { FC } from "react";
import { UserProfile, UserProfile_with_signin } from "./user_profile";  

export const Header: FC<{ show_signin: string }> = ({ show_signin }) => {
  return (
    <header className="px-3 py-1 md:px-3 md:py-1 flex md:grid md:grid-cols-6 h-[48px] md:h-[48px]">
        <div>
            <a href="/">
                <img src="https://le-cdn.website-editor.net/s/1410911f48024acea37d53b26049d3e6/dms3rep/multi/opt/Cname-9472def4-280w.PNG?Expires=1747773396&amp;Signature=ifKI8XOd~FvaJ-4wysYRdNK40uCeELUr0H8Fa00W6dUBIKdet9fWRi6WBPB1uumHjbA3q2E7~MHuaTeSNQj0W47-k5nBsQ~ITztAWUPVqx0XCriEcGcIm60SDyv2q6UQEvJidH9EYX1VMO0lXOa4Pgar~tDcGc60uX5RoUQm58oms5FuLDM35p~5NyC2d6BZzd13C~lqMjkpZ9LKMwS~KqmjA7PqRRX1yTOAsAYUqY3mn-BpEN1dERQmfSfGtGuyRVDz9RUa4zjdu5oeR65VvXr53oZRu~wYD7fLxt-xsY2iVl2QNuhHl1ff~tEema2eTTtOkYFh4q9H4AJ8l29GWg__&amp;Key-Pair-Id=K2NXBXLF010TJW" id="1266195280" data-dm-image-path="https://cdn.website-editor.net/1410911f48024acea37d53b26049d3e6/dms3rep/multi/Cname-9472def4.PNG"  height="46" width="100" />            
            </a>
        </div>

        <div className="flex-1 flex items-end justify-end md:col-start-6">
          {
            show_signin == "true" ? <UserProfile_with_signin /> : <UserProfile />
          }
        </div>
    </header>
  );
};
